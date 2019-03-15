#!/usr/bin/python3

import sys, os
myPath = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, myPath + '/../usr/lib/linuxmint/mintUpdate/')
from Classes import PRIORITY_UPDATES


def test_always_succeeds():
    assert 3 == 3
