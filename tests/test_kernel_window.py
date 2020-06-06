#!/usr/bin/python3

import sys, os
myPath = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, myPath + '/../usr/lib/linuxmint/mintUpdate/')

from datetime import datetime
from Classes import *
from mintUpdate import *
from checkAPT import *

# Test KernelVersion object
def test_kernel_version_series_comparison():
    version1 = KernelVersion ("4.8.0-2-generic")
    version2 = KernelVersion ("4.15.0-43-generic")
    version3 = KernelVersion ("4.15.0-44-generic")
    assert version1.version_id < version2.version_id
    assert version2.version_id < version3.version_id
    assert version1.series < version2.series
    assert version2.series == version3.series

