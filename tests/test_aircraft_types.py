import numpy as np
import pandas as pd

from doppler import aircraft_types as at


def test_class_from_description():
    assert at.class_from_description("L2J") == at.JET
    assert at.class_from_description("L1P") == at.PROP
    assert at.class_from_description("L2T") == at.PROP
    assert at.class_from_description("H1T") == at.PROP     # rotorcraft: harmonic stacks
    assert at.class_from_description("L1E") == at.PROP
    for bad in (None, "", "L2", "L2X", 5):
        assert at.class_from_description(bad) == at.UNKNOWN


def test_lookup_registry_type_table_and_unknowns():
    reg = pd.DataFrame({"icao24": ["A1B2C3", "d4e5f6", "aaaaaa", "bbbbbb"],
                        "typecode": ["b738", "C172", "ZZZZ", None],
                        "icaoaircrafttype": ["", "L1P", "", ""]})
    tt = pd.DataFrame({"typecode": ["B738", "C172"], "description": ["L2J", "L1P"]})
    lk = at.build_lookup(reg, tt)
    types, cls = at.classify_icao24(["a1b2c3", "D4E5F6", "aaaaaa", "bbbbbb", "ffffff"], lk)
    assert types.tolist() == ["B738", "C172", "ZZZZ", "", ""]
    assert cls.tolist() == [at.JET, at.PROP, at.UNKNOWN, at.UNKNOWN, at.UNKNOWN]
    # without a type table, the registry's own description is used
    _, cls2 = at.classify_icao24(["d4e5f6", "a1b2c3"], at.build_lookup(reg))
    assert cls2.tolist() == [at.PROP, at.UNKNOWN]


def test_compatible():
    ev = np.array([at.PROP, at.PROP, at.JET, at.UNKNOWN])
    tr = np.array([at.PROP, at.JET, at.UNKNOWN, at.JET])
    assert at.compatible(ev, tr).tolist() == [True, False, True, True]
