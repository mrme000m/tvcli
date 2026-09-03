import sys
import unittest

ROOT = __file__.rsplit("/tests/", 1)[0]
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
