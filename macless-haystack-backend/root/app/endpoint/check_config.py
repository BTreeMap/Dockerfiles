#!/usr/bin/env python3

import logging
import os
import sys

import config

logger = logging.getLogger()

if os.path.exists(config.getConfigFile()):
    logger.info("Config file exists at: {}".format(config.getConfigFile()))
    sys.exit(0)
else:
    logger.info("Config file does not exist at: {}".format(config.getConfigFile()))
    sys.exit(1)
