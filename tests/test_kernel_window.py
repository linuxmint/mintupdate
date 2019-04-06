#!/usr/bin/python3

import sys, os
myPath = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, myPath + '/../usr/lib/linuxmint/mintUpdate/')

from datetime import datetime
from Classes import *
from mintUpdate import *
from checkAPT import *

# Test if the distribution EOL date is found
def test_eol_is_found():
    (is_eol, show_eol_warning, eol_date) = MintUpdate.get_eol_status()
    assert isinstance(eol_date, datetime)
    assert eol_date
    assert eol_date.year > 2010

# Test KernelVersion object
def test_kernel_version_series_comparison():
    version1 = KernelVersion ("4.8.0-2-generic")
    version2 = KernelVersion ("4.15.0-43-generic")
    version3 = KernelVersion ("4.15.0-44-generic")
    assert version1.version_id < version2.version_id
    assert version2.version_id < version3.version_id
    assert version1.series < version2.series
    assert version2.series == version3.series

