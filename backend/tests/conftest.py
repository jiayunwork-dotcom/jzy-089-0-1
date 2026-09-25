import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sample_model import build_sample_model
from app.modeling import validate_model
from app.translator import Translator


@pytest.fixture(scope="session")
def model():
    return validate_model(build_sample_model())


@pytest.fixture()
def tr(model):
    return Translator(model)
