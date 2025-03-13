#!/usr/bin/env python3

import logging
import os
import sys

import mh_config

logger = logging.getLogger()

if os.path.exists(mh_config.getConfigFile()):
    logger.info("Config file exists at: {}".format(mh_config.getConfigFile()))
    sys.exit(0)
else:
    logger.info("Config file does not exist at: {}".format(mh_config.getConfigFile()))
    sys.exit(1)
