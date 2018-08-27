#!/usr/bin/env python3

""" Raspberry Pi Security Camera for SmartThings

Copyright 2018 Paul Witt <paulrwitt@gmail.com>

Dependencies: python-twisted, cv2, pyimagesearch

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License. You may obtain a copy of the
License at:

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
"""

import argparse
import logging
import cv2
import urllib
import imutils
import json
import os
import sys
import uuid
import boto3

from datetime import datetime, timedelta
from time import time, sleep
from botocore.exceptions import BotoCoreError, ClientError
from imutils.video import VideoStream
from picamera.array import PiRGBArray
from picamera import PiCamera
from twisted.web import server, resource
from twisted.internet import reactor
from twisted.internet.defer import succeed
from twisted.internet.protocol import DatagramProtocol
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted.web.iweb import IBodyProducer
from twisted.web._newclient import ResponseFailed
from zope.interface import implementer

# setting up logging for this script
_LEVEL = logging.INFO
_FORMAT = "%(asctime)-15s [%(levelname)-8s] : %(lineno)d : %(name)s.%(funcName)s : %(message)s"
logging.basicConfig(format=_FORMAT, level=_LEVEL)
LOG = logging.getLogger()

SSDP_PORT = 1900
SSDP_ADDR = '239.255.255.250'
UUID = 'd1c58eb4-9220-11e4-96fa-123b93f75cba'
SEARCH_RESPONSE = 'HTTP/1.1 200 OK\r\nCACHE-CONTROL:max-age=30\r\nEXT:\r\nLOCATION:%s\r\nSERVER:Linux, UPnP/1.0, Pi_Camera/1.0\r\nST:%s\r\nUSN:uuid:%s::%s\r\n'

try:
    SESSION = boto3.session.Session()
    S3 = SESSION.resource('s3')
except (BotoCoreError, ClientError) as error:
    LOG.error("ERROR: Unable to create AWS S3 resource, AWS returned an error.")


def parse_args(args):
    """ Parse the arguments passed to this script """
    argp = argparse.ArgumentParser()
    argp.add_argument('--conf', required=True, help="path to JSON config file")
    return argp.parse_args(args)


def determine_ip_for_host(host):
    """Determine local IP address used to communicate with a particular host"""
    test_sock = DatagramProtocol()
    test_sock_listener = reactor.listenUDP(0, test_sock) # pylint: disable=no-member
    test_sock.transport.connect(host, 1900)
    my_ip = test_sock.transport.getHost().host
    test_sock_listener.stopListening()
    return my_ip


@implementer(IBodyProducer)
class StringProducer(object):
    """Writes an in-memory string to a Twisted request"""

    def __init__(self, body):
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer): # pylint: disable=invalid-name
        """Start producing supplied string to the specified consumer"""
        consumer.write(self.body)
        return succeed(None)

    def pauseProducing(self): # pylint: disable=invalid-name
        """Pause producing - no op"""
        pass

    def stopProducing(self): # pylint: disable=invalid-name
        """ Stop producing - no op"""
        pass


class SSDPServer(DatagramProtocol):
    """Receive and response to M-SEARCH discovery requests from SmartThings hub"""

    def __init__(self, interface='', status_port=0, device_target=''):
        self.interface = interface
        self.device_target = device_target
        self.status_port = status_port
        self.port = reactor.listenMulticast(SSDP_PORT, self, listenMultiple=True) # pylint: disable=no-member
        self.port.joinGroup(SSDP_ADDR, interface=interface)
        reactor.addSystemEventTrigger('before', 'shutdown', self.stop) # pylint: disable=no-member

    def datagramReceived(self, data, address):
        try:
            header, _ = data.decode().split('\r\n\r\n')[:2]
        except ValueError:
            return
        lines = header.split('\r\n')
        cmd = lines.pop(0).split(' ')
        lines = [x.replace(': ', ':', 1) for x in lines]
        lines = [x for x in lines if len(x) > 0]
        headers = [x.split(':', 1) for x in lines]
        headers = dict([(x[0].lower(), x[1]) for x in headers])

        LOG.debug('SSDP command %s %s - from %s:%d with headers %s', cmd[0], cmd[1], address[0], address[1], headers)

        search_target = ''
        if 'st' in headers:
            search_target = headers['st']

        if cmd[0] == 'M-SEARCH' and cmd[1] == '*':
            if search_target in self.device_target:
                LOG.info('SSDP command %s %s - from %s:%d with headers %s', cmd[0], cmd[1], address[0], address[1], headers)
                LOG.info('Received %s %s for %s from %s:%d', cmd[0], cmd[1], search_target, address[0], address[1])
                url = 'http://%s:%d/status' % (determine_ip_for_host(address[0]), self.status_port)
                response = SEARCH_RESPONSE % (url, search_target, UUID, self.device_target)
                self.port.write(bytes(response, 'utf-8'), address)
            else:
                LOG.debug('%s not in %s', search_target, self.device_target)
        else:
            LOG.debug('Ignored SSDP command %s %s', cmd[0], cmd[1])

    def stop(self):
        """Leave multicast group and stop listening"""
        self.port.leaveGroup(SSDP_ADDR, interface=self.interface)
        self.port.stopListening()


class StatusServer(resource.Resource):
    """HTTP server that serves the status of the camera to the
       SmartThings hub"""
    isLeaf = True
    def __init__(self, device_target, subscription_list, camera_status, camera_image):
        self.device_target = device_target
        self.subscription_list = subscription_list
        self.camera_status = camera_status
        self.camera_image = camera_image
        resource.Resource.__init__(self)

    def render_SUBSCRIBE(self, request): # pylint: disable=invalid-name
        """Handle subscribe requests from ST hub - hub wants to be notified
           of status updates"""
        headers = request.getAllHeaders()
        LOG.info("SUBSCRIBE: %s", headers)
        if b'callback' in headers:
            cb_url = headers[b'callback'][1:-1].decode()

            if not cb_url in self.subscription_list:
                self.subscription_list[cb_url] = {}
                LOG.info('Added subscription %s', cb_url)
            else:
                LOG.info('Refreshed subscription %s', cb_url)

            self.subscription_list[cb_url]['expiration'] = time() + 24 * 3600

        imageurl = self.camera_image['last_image']
        if self.camera_status['last_state'] == 'inactive':
            cmd = 'status-inactive'
        else:
            cmd = 'status-active'
        msg = '<msg><cmd>%s</cmd><usn>uuid:%s::%s</usn><imageurl>%s</imageurl></msg>' % (cmd, UUID, self.device_target, imageurl)
        return bytes(msg, 'utf-8')

    def render_GET(self, request): # pylint: disable=invalid-name
        """Handle polling requests from ST hub"""
        LOG.info("GET: %s", request.path)
        if request.path == b'/status':
            imageurl = self.camera_image['last_image']
            if self.camera_status['last_state'] == 'inactive':
                cmd = 'status-inactive'
            else:
                cmd = 'status-active'
            msg = '<msg><cmd>%s</cmd><usn>uuid:%s::%s</usn><imageurl>%s</imageurl></msg>' % (cmd, UUID, self.device_target, imageurl)
            LOG.info("Polling request from %s for %s - returned %s (%s)",
                     request.getClientIP(),
                     request.path,
                     cmd,
                     imageurl)
            return bytes(msg, 'utf-8')

        LOG.info("Received bogus request from %s for %s",
                 request.getClientIP(),
                 request.path)
        return ""


class MonitorCamera(object):
    """Monitors camera status, generating notifications whenever its state changes"""
    def __init__(self, device_target, subscription_list, camera_status, camera_image, conf): # pylint: disable=too-many-arguments
        self.device_target = device_target
        self.subscription_list = subscription_list
        self.camera_status = camera_status
        self.camera_image = camera_image

        self.avg = None
        self.min_area = conf["min_area"]
        self.draw_boxes = conf["draw_boxes"]
        self.basepath = conf["basepath"]
        self.s3bucket = conf["s3bucket"]
        self.s3folder = conf["s3folder"]
        self.baseimageurl = conf["baseimageurl"]
        self.fileext = conf["fileext"]
        self.delta_thresh = conf["delta_thresh"]
        self.polling_freq = conf["polling_freq"]

        # Define the video settings
        self.width = conf["resolution"][0]
        self.height = conf["resolution"][1]

        # initialize the camera and grab a reference to the raw camera capture
        LOG.info("Initializing the video stream...")
        self.camera = PiCamera()
        self.camera.resolution = tuple(conf["resolution"])
        self.camera.framerate = 30.0
        self.camera.video_stabilization = True
        self.rawCapture = PiRGBArray(self.camera, size=tuple(conf["resolution"]))

        LOG.info("Warming up the camera...")
        sleep(conf["camera_warmup_time"])

        current_state = 'inactive'
        reactor.callLater(self.polling_freq, self.check_state, current_state) # pylint: disable=no-member

    def check_state(self, current_state):
        self.current_state = current_state
        notify = False
        self.camera.capture(self.rawCapture, format="bgr", use_video_port=True)

        # grab the raw NumPy array representing the image
        frame = self.rawCapture.array
        timestamp = datetime.now()

        # resize the frame and convert it to grayscale
        try:
            frame = imutils.resize(frame, width=self.width, height=self.height)
        except AttributeError:
            LOG.info("ERROR: Resizing the frame threw an error.")
            reactor.callLater(self.polling_freq, self.check_state, current_state) # pylint: disable=no-member
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        # if the average frame is None, initialize it
        if self.avg is None:
            LOG.info("Starting background model...")
            self.rawCapture.truncate(0)
            self.avg = gray.copy().astype("float")

        else:
            # accumulate the weighted average between the current frame and
            # previous frames, then compute the difference between the current
            # frame and running average
            cv2.accumulateWeighted(gray, self.avg, 0.5)
            frameDelta = cv2.absdiff(gray, cv2.convertScaleAbs(self.avg))

            # threshold the delta image, dilate the thresholded image to fill
            # in holes, then find contours on thresholded image
            thresh = cv2.threshold(frameDelta, self.delta_thresh, 255, cv2.THRESH_BINARY)[1]
            thresh = cv2.dilate(thresh, None, iterations=2)
            cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cnts = cnts[0] if imutils.is_cv2() else cnts[1]

            # draw the text and timestamp on the frame
            ts = timestamp.strftime("%A %d %B %Y %I:%M:%S%p")
            self.camera.annotate_text = ts
            #cv2.putText(frame, ts, (10, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

            # loop over the contours
            for c in cnts:
                # if the contour is too small, ignore it
                if cv2.contourArea(c) < self.min_area:
                    self.rawCapture.truncate(0)
                    continue

                if self.draw_boxes:
                    # compute the bounding box for the contour and draw it on the frame
                    (x, y, w, h) = cv2.boundingRect(c)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                # if we have a contour of the right size we're now actively detecting motion
                if current_state == "inactive":
                    current_state = "active"
                    LOG.info('State changed from %s to %s', self.camera_status['last_state'], current_state)
                    self.camera_status['last_state'] = current_state
                    notify = True

            # no contours found - we're now inactive
            if not cnts and current_state == "active":
                current_state = "inactive"
                LOG.info('State changed from %s to %s', self.camera_status['last_state'], current_state)
                self.camera_status['last_state'] = current_state
                notify = True

            # write the frame image to disk
            if current_state == "active":
                # write it locally first
                filename = self.get_path(self.basepath, self.fileext, timestamp)
                cv2.imwrite(filename, frame)

                if notify:
                    # Now write it to S3 so our device handler can get to it
                    s3filename = self.get_path(self.s3folder, self.fileext, timestamp)
                    LOG.info("Uploading %s to S3 in bucket %s with key %s", filename, self.s3bucket, s3filename)
                    try:
                        S3.meta.client.upload_file(filename, self.s3bucket, s3filename, ExtraArgs={'ACL': 'public-read', 'ContentType': 'image/jpeg'})
                    except (BotoCoreError, ClientError) as error:
                        LOG.error("ERROR: Unable to upload file, AWS returned an error.")

                    # This will be sent back to SmartThings
                    imageurl = "/{}/{}".format(self.s3bucket, s3filename)
                    LOG.info("Setting last_image to https://s3.amazonaws.com%s", imageurl)
                    self.camera_image['last_image'] = imageurl

            if notify:
                self.notify_hubs()

        # Schedule next check
        reactor.callLater(self.polling_freq, self.check_state, current_state) # pylint: disable=no-member
        self.rawCapture.truncate(0)

    def get_path(self, basepath, fileext, timestamp):
        # construct the file path
        return "{}/{}{}".format(basepath, timestamp.strftime("%Y-%m-%d-%H-%M-%S"), fileext)

    def notify_hubs(self):
        """Notify the subscribed SmartThings hubs that a state change has occurred"""
        if self.camera_status['last_state'] == 'inactive':
            cmd = 'status-inactive'
        else:
            cmd = 'status-active'

        if not self.subscription_list:
            LOG.info('No current subscription list')

        for subscription in self.subscription_list:
            LOG.info('Subscription: %s', subscription)
            if self.subscription_list[subscription]['expiration'] > time():
                LOG.info("Notifying hub %s", subscription)
                msg = '<msg><cmd>%s</cmd><usn>uuid:%s::%s</usn><imageurl>%s</imageurl></msg>' % (cmd, UUID, self.device_target, self.camera_image['last_image'])
                body = StringProducer(bytes(msg, 'utf-8'))
                agent = Agent(reactor)
                req = agent.request(
                    b'POST',
                    bytes(subscription, 'utf-8'),
                    Headers({'CONTENT-LENGTH': [str(len(msg))]}),
                    body)
                req.addCallback(self.handle_response)
                req.addErrback(self.handle_error)

    def handle_response(self, response): # pylint: disable=no-self-use
        """Handle the SmartThings hub returning a status code to the POST.
           This is actually unexpected - it typically closes the connection
           for POST/PUT without giving a response code."""
        if response.code == 202:
            LOG.info("Status update accepted")
        else:
            LOG.error("Unexpected response code: %s", response.code)

    def handle_error(self, response): # pylint: disable=no-self-use
        """Handle errors generating performing the NOTIFY. There doesn't seem
           to be a way to avoid ResponseFailed - the SmartThings Hub
           doesn't generate a proper response code for POST or PUT, and if
           NOTIFY is used, it ignores the body."""
        if isinstance(response.value, ResponseFailed):
            LOG.debug("Response failed (expected)")
        else:
            LOG.error("Unexpected response: %s", response)


def main():
    """Main function to handle use from command line"""

    args = parse_args(sys.argv[1:])

    if not os.path.isfile(args.conf):
        LOG.error("Configuration file {} not found".format(args.conf))
        return False

    # load the configuration
    conf = json.load(open(args.conf))

    # set log level
    if conf["debug"]:
        LOG.setLevel(logging.DEBUG)

    device_target = 'urn:schemas-upnp-org:device:RPi_Security_Camera:{}'.format(conf['device_index'])
    LOG.info('device_target set to %s', device_target)

    subscription_list = {}
    camera_status = {'last_state': 'inactive'}
    camera_image = {'last_image': '/raspicameras/front/blank.jpg'}

    # SSDP server to handle discovery
    SSDPServer(status_port=conf["http_port"], device_target=device_target)

    # HTTP site to handle subscriptions/polling
    status_site = server.Site(StatusServer(device_target, subscription_list, camera_status, camera_image))
    reactor.listenTCP(conf["http_port"], status_site) # pylint: disable=no-member

    LOG.info('Initialization complete')

    # Monitor camera state and send notifications on state change
    MonitorCamera(device_target=device_target,
                  subscription_list=subscription_list,
                  camera_status=camera_status,
                  camera_image=camera_image,
                  conf=conf)

    reactor.run() # pylint: disable=no-member

if __name__ == "__main__":
    main()
