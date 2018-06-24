#!/usr/bin/env python3

# import the necessary packages
import argparse
import cv2
import imutils
import json
import logging
import os
import sys
import time
import uuid

from datetime import datetime, timedelta
from imutils.video import VideoStream
from picamera.array import PiRGBArray
from picamera import PiCamera

# setting up logging for this script
_LEVEL = logging.INFO
_FORMAT = "%(asctime)-15s [%(levelname)-8s] : %(lineno)d : %(name)s.%(funcName)s : %(message)s"
logging.basicConfig(format=_FORMAT, level=_LEVEL)
LOG = logging.getLogger()


def parse_args(args):
    """ Parse the arguments passed to this script """
    argp = argparse.ArgumentParser()
    argp.add_argument('--conf', required=True, help="path to JSON config file")
    return argp.parse_args(args)


def get_path(basePath, ext, timestamp):
    # construct the file path
    return "{}/{}{}".format(basePath, timestamp.strftime("%A %d %B %Y %I-%M-%S%p"), ext)


def get_fps(camera):
    # Number of frames to capture
    num_frames = 120

    LOG.info("Capturing {} frames to calculate fps.".format(num_frames))

    # Start time
    start = time.time()

    # Grab a few frames
    for i in range(0, num_frames):
        for f in camera.capture_continuous(rawCapture, format="bgr", use_video_port=True):
            frame = f.array
            break

    # End time
    end = time.time()

    # Time elapsed
    seconds = end - start
    print("Time taken : {} seconds".format(seconds))

    # Calculate frames per second
    fps  = num_frames / seconds
    print("Estimated frames per second : {}".format(fps))
    return fps


def main():
    """ Main """

    args = parse_args(sys.argv[1:])

    if not os.path.isfile(args.conf):
        LOG.error("Configuration file {} not found".format(args.conf))
        return False

    # load the configuration
    conf = json.load(open(args.conf))

    # initialize the camera and grab a reference to the raw camera capture
    LOG.info("Initializing the video stream...")
    camera = PiCamera()
    camera.resolution = tuple(conf["resolution"])
    camera.framerate = conf["fps"]
    rawCapture = PiRGBArray(camera, size=tuple(conf["resolution"]))

    LOG.info("Warming up the camera...")
    time.sleep(conf["camera_warmup_time"])

    fps = get_fps(camera)

    avg = None
    lastUploaded = datetime.now()

    # Define the codec and create VideoWriter object
    fourcc = cv2.VideoWriter_fourcc(*'{}'.format(conf["codec"]))
    width = conf["resolution"][0]
    height = conf["resolution"][1]
    LOG.info("Recording using the {} codec at {}x{} and {} fps".format(conf["codec"], width, height, round(fps,2)))
    size = (width, height)
    out = None

    # Initialize the window
    if conf["show_video"]:
        cv2.namedWindow('Security Feed', cv2.WINDOW_NORMAL)
        #cv2.moveWindow('Security Feed', 0, 0)

    # capture frames from the camera
    for f in camera.capture_continuous(rawCapture, format="bgr", use_video_port=True):
        try:
            # grab the raw NumPy array representing the image and initialize
            # the timestamp and occupied/unoccupied text
            frame = f.array
            timestamp = datetime.now()

            # resize the frame, convert it to grayscale, and blur it
            frame = imutils.resize(frame, width=width, height=height)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            # if the average frame is None, initialize it
            if avg is None:
                LOG.info("Starting background model...")
                rawCapture.truncate(0)
                avg = gray.copy().astype("float")
                text = "Unoccupied"
                video_start = None
                in_window = False
                continue

            # accumulate the weighted average between the current frame and
            # previous frames, then compute the difference between the current
            # frame and running average
            cv2.accumulateWeighted(gray, avg, 0.5)
            frameDelta = cv2.absdiff(gray, cv2.convertScaleAbs(avg))

            # threshold the delta image, dilate the thresholded image to fill
            # in holes, then find contours on thresholded image
            thresh = cv2.threshold(frameDelta, conf["delta_thresh"], 255, cv2.THRESH_BINARY)[1]
            thresh = cv2.dilate(thresh, None, iterations=2)
            cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cnts = cnts[0] if imutils.is_cv2() else cnts[1]

            # loop over the contours
            for c in cnts:
                # if the contour is too small, ignore it
                if cv2.contourArea(c) < conf["min_area"]:
                    continue

                if conf['draw_boxes']:
                    # compute the bounding box for the contour, draw it on the frame, and update the text
                    (x, y, w, h) = cv2.boundingRect(c)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                if not text == "Occupied":
                    text = "Occupied"
                    video_start = datetime.now()
                    filename = get_path(conf['basepath'], conf['ext'], timestamp)

                    if out:
                        out.release()
                        out = None

                    out = cv2.VideoWriter(filename, fourcc, fps, size)

            # draw the text and timestamp on the frame
            ts = timestamp.strftime("%A %d %B %Y %I:%M:%S%p")
            cv2.putText(frame, ts, (10, frame.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

            if video_start:
                if (timestamp - video_start).total_seconds() < conf['minimum_video_length']:
                    in_window = True
                else:
                    in_window = False

            # check to see if the room is occupied
            if text == "Occupied":
                out.write(frame)

            if not cnts and not in_window:
                text = "Unoccupied"

            if not in_window:
                video_start = None

            # check to see if the frames should be displayed to screen
            if conf["show_video"]:
                # display the security feed
                cv2.imshow("Security Feed", frame)
                key = cv2.waitKey(1) & 0xFF

                # if the `q` key is pressed, break from the lopp
                if key == ord("q"):
                    break

            rawCapture.truncate(0)

        except KeyboardInterrupt:
            break

    if out:
        out.release()
    out = None
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()
