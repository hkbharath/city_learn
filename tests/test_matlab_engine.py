import matlab.engine


def test_matlab_engine():
    eng = matlab.engine.start_matlab()
    assert eng.sqrt(4.0) == 2.0
    eng.quit()