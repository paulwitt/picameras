#!/usr/bin/env python3

import argparse
import logging
import json
import os
import sys
import time

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


def main():
    """Main function to handle use from command line"""

    args = parse_args(sys.argv[1:])

    if not os.path.isfile(args.conf):
        LOG.error("Configuration file {} not found".format(args.conf))
        return False

    # load the configuration
    conf = json.load(open(args.conf))

    now = time.time()
    path = conf["basepath"]
    daysold = conf["daysold"]

    for f in os.listdir(path):
        thisfile = os.path.join(path, f)
        if os.stat(thisfile).st_mtime < now - daysold * 86400:
            if os.path.isfile(thisfile):
                LOG.info('Deleting: %s', thisfile)
                os.remove(thisfile)
            else:
                LOG.info('%s is not a file', thisfile)
        else:
            LOG.info('%s is newer than %s days old', thisfile, daysold)

if __name__ == "__main__":
    main()
