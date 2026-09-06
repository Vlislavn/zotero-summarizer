"""The Python file-size ceiling has no grandfather escape hatch."""
from tools.precommit import check_file_loc


def test_hard_500_line_cutoff(tmp_path):
    path = tmp_path / "module.py"
    path.write_text("x = 1\n" * 500)
    assert check_file_loc.main([str(path)]) == 0
    path.write_text("x = 1\n" * 501)
    assert check_file_loc.main([str(path)]) == 1
