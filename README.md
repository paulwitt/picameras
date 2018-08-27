# RPi Security Cameras

## RPi Security Cameras App
This is the SmartThings user created SmartApp and Device handlers for Nest.
The SmartApp and Device Handlers work together to provide integration to the SmartThings ecosystem using Nest's Official API.

## Author
* @paulwitt
* Various others

## About
Got the idea for this project from this posting:
* https://www.hackster.io/juano2310/computer-vision-as-motion-sensor-for-smartthings-803341

But it doesn't work.  So I tracked down what I'm pretty sure is the repo that gave him the idea:
* https://github.com/rllynch/pi_garage_smartthings

So I combined the two and started going through tutorials so their code would make more sense.  Links:
* https://docs.smartthings.com/en/latest/cloud-and-lan-connected-device-types-developers-guide/working-with-images.html
* https://www.pyimagesearch.com/2015/05/25/basic-motion-detection-and-tracking-with-python-and-opencv/
* https://www.pyimagesearch.com/2015/06/01/home-surveillance-and-motion-detection-with-the-raspberry-pi-python-and-opencv/

## How-to (at least how I did it)
Needed:
* Raspberry Pi - I use a Pi Zero W - just get the starter kit
* Camera - I use a nightvision camera
* Case - I just use a small ABS plastic enclosure
* Mount - I use the mount from a cheap fake security camera
* AWS account
* S3 bucket with public access (this will cost you money)

Recommended:
* Battery backup - I use a Pi Zero UpTime UPS
* Hammer headers - Easiest way to connect the UPS

Install in SmartThings IDE:
* Add paulwitt/cameras to your GitHub integration
* Install the Device Handler and App from the repo

Setup your AWS/S3 account:
* Not going to cover the specifics here.  If you need help with this piece you'll have to google it.

Install on your Pi:
* Note: The script will run as root so I usually usually 'sudo -i' before I install libraries through pip3
* Install raspbian and update it
* Install python3 and pip3
* Install OpenCV (https://www.pyimagesearch.com/2016/04/18/install-guide-raspberry-pi-3-raspbian-jessie-opencv-3/)
* Install boto3 and twisted via pip3 as root
* Create a 'camera' folder in your home folder
* Copy the 'conf-pizero.json' and 'smartthings-pi.py' file into that folder (use wget from this repo)
* Set the python script to executable
* Add this to your /etc/rc.local file: (sleep 10;python3 /home/pi/camera/smartthings-pi.py --conf /home/pi/camera/conf-pizero.json)&
* Update the conf-pizero.json file with the 'basepath', 's3bucket', 's3folder', and 'baseimageurl' for your local and s3 setup
* Create and update /home/pi/.aws/config and /home/pi/.aws/credentials

Notes:
* This does log to /var/log/syslog
* It will write images to 'basepath' whenever it sees motion.  This can be a lot of images.
* It will write images to S3 when it goes from inactive (no motion) to active.  This is the image that will display in the mobile app.

Known issues:
* There's not enough error trapping around writing these files.
* It seems to require a static IP on your Pi or it won't reconnect properly if there's a network issue or a crash.
* The first image when it detects motion isn't the best image of the motion.
