/**
 *  Raspberry Pi - Security Camera (Device Handler)
 *
 *  Copyright 2018 Paul Witt <paul@bully-pulpit.com>
 *
 *  Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
 *  in compliance with the License. You may obtain a copy of the License at:
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed
 *  on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License
 *  for the specific language governing permissions and limitations under the License.
 *
 */
metadata {
    definition (name: "RPi Security Camera", namespace: "rpi_camera", author: "Paul Witt") {
        capability "Motion Sensor"
        capability "Image Capture"
        capability "Sensor"
        capability "Refresh"
        command "subscribe"
    }

    simulator {
        status "active": "motion: 1"
        status "inactive": "motion: 0"
    }

    tiles(scale: 2) {
        multiAttributeTile(name:"motion", type: "generic", width: 6, height: 4){
            tileAttribute ("device.motion", key: "PRIMARY_CONTROL") {
                attributeState "active", label:'motion', icon:"st.motion.motion.active", backgroundColor:"#53a7c0"
                attributeState "inactive", label:'no motion', icon:"st.motion.motion.inactive", backgroundColor:"#ffffff"
            }
        }

        carouselTile("cameraDetails", "device.image", width: 4, height: 3) { }

        standardTile("refresh", "device.refresh", inactiveLabel: false, decoration: "flat", width: 2, height: 2) {
            state "default", action:"refresh.refresh", icon:"st.secondary.refresh"
        }

        main "motion"
        details (["motion", "cameraDetails", "refresh"])
    }
}

// parse events into attributes
void parse(String description) {
    def usn = getDataValue('ssdpUSN')
    def parsedEvent = parseLanMessage(description)
    log.debug "Parsing raspberry pi security camera ${device.deviceNetworkId} ${usn}"
    if (parsedEvent['body'] != null) {
        def xmlTop = new XmlSlurper().parseText(parsedEvent.body)
        def cmd = xmlTop.cmd[0]
        def targetUsn = xmlTop.usn[0].toString()
        log.debug "Target USN is ${targetUsn}"
        def imageurl = xmlTop.imageurl[0].toString()
        log.debug "Camera returned image url ${imageurl}"
        getAndStoreImage(imageurl)
        log.debug "Processing command ${cmd} for ${targetUsn}"
        parent.getChildDevices().each { child ->
            def childUsn = child.device.getDataValue("ssdpUSN").toString()
            log.debug "childUsn for ${child.device.label} is ${childUsn}"
            if (childUsn == targetUsn) {
                if (cmd == 'refresh') {
                    log.debug "Instructing ${child.device.label} to refresh"
                    refresh()
                } else if (cmd == 'status-active') {
                    def value = 'active'
                    log.debug "Updating ${child.device.label} to ${value}"
                    sendEvent(name: 'motion', value: value)
                } else if (cmd == 'status-inactive') {
                    def value = 'inactive'
                    log.debug "Updating ${child.device.label} to ${value}"
                    sendEvent(name: 'motion', value: value)
                }
            }
        }
    }

}

def getAndStoreImage(imageurl) {

    def uri = "https://s3.amazonaws.com"
    def params = [
        uri: uri,
        path: imageurl
    ]

    log.debug "Attempting to download image ${uri}${imageurl}"

    try {
        httpGet(params) { response ->
            // we expect a content type of "image/jpeg" from the third party in this case
            if (response.status == 200 && response.headers.'Content-Type'.contains("image/jpeg")) {
                def imageBytes = response.data
                if (imageBytes) {
                    def name = getImageName()
                    try {
                        storeImage(name, imageBytes)
                    } catch (e) {
                        log.error "Error storing image ${name}: ${e}"
                    }

                }
            } else {
                log.error "Image response not successful or not a jpeg response"
            }
        }
    } catch (err) {
        log.debug "Error making request: $err"
    }

}

def getImageName() {
    return java.util.UUID.randomUUID().toString().replaceAll('-','')
}

def refresh() {
    log.debug "Executing 'refresh'"
    subscribeAction()
    getRequest()
}

def subscribe() {
    subscribeAction()
}

private subscribeAction(callbackPath="") {
    log.debug "Subscribe requested"
    def hubip = device.hub.getDataValue("localIP")
    def hubport = device.hub.getDataValue("localSrvPortTCP")
    def ssdpPath = getDataValue("ssdpPath")
    def hostaddress = getHostAddress()
    def callback = "<http://${hubip}:${hubport}/notify$callbackPath>"

    log.debug "Sending subscription info with ${ssdpPath} to ${hostaddress} with ${callback}"
    def result = new physicalgraph.device.HubAction(
        method: "SUBSCRIBE",
        path: ssdpPath,
        headers: [
            HOST: hostaddress,
            CALLBACK: callback,
            NT: "upnp:event",
            TIMEOUT: "Second-3600"])
    result
}

def getRequest() {
    log.debug "Sending request for ${path} from ${device.deviceNetworkId}"
    new physicalgraph.device.HubAction(
        'method': 'GET',
        'path': getDataValue("ssdpPath"),
        'headers': [
            'HOST': getHostAddress(),
        ], device.deviceNetworkId)
}

private getHostAddress() {
    def host = getDataValue("ip") + ":" + getDataValue("port")
    return host
}
