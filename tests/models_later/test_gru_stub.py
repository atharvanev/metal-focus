import pytest

from common.model_api import LiveModel, TrainableModel
from models_later.gru import GRUModel


def test_gru_stub_satisfies_interface_but_is_not_built():
    m = GRUModel()
    assert isinstance(m, LiveModel) and isinstance(m, TrainableModel)
    with pytest.raises(NotImplementedError):
        m.step(None)
