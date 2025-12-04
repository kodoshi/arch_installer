"""Root pytest configuration for all tests.

Provides fixtures that are explicitly requested by tests - no autouse
fixtures to avoid side effects and make test dependencies clear.
"""

import os
import sys

import pytest

sys.path.append(os.getcwd())
