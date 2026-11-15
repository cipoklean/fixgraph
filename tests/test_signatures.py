from fixgraph.signatures import normalize, signature_id, node_id


def test_same_error_different_paths_same_signature():
    a = "ModuleNotFoundError: No module named 'distutils' at /home/alice/x.py"
    b = "ModuleNotFoundError: No module named 'distutils' at C:\\Users\\bob\\y.py"
    assert normalize(a) == normalize(b)


def test_versions_stripped():
    a = "error: externally-managed-environment python 3.11.2"
    b = "error: externally-managed-environment python 3.12.7"
    assert normalize(a) == normalize(b)


def test_line_numbers_and_hashes_stripped():
    a = 'File "app.py", line 42, in <module> abc123def456 failed'
    b = 'File "app.py", line 99, in <module> 999888aaa777 failed'
    assert normalize(a) == normalize(b)


def test_distinct_errors_distinct_ids():
    id_a, _ = signature_id("ModuleNotFoundError: No module named 'distutils'")
    id_b, _ = signature_id("error: externally-managed-environment")
    assert id_a != id_b


def test_deterministic_id():
    id_a, _ = signature_id("some error 123")
    id_b, _ = signature_id("some error 123")
    assert id_a == id_b


def test_node_id_namespaced():
    assert node_id("sig", "x") != node_id("fix", "x")


def test_traceback_tail_kept():
    raw = "Traceback (most recent call last):\n" + "\n".join(
        f'  File "/x/f{i}.py", line {i}, in m{i}' for i in range(10)
    ) + "\nModuleNotFoundError: No module named 'distutils'"
    sig = normalize(raw)
    assert "modulenotfounderror" in sig
    assert "/x/f0" not in sig
