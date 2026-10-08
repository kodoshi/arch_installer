"""Puts the repository root on the import path, so tests can import the tests package."""

import os
import sys

sys.path.append(os.getcwd())
