from atracsys_sync.analysis_selection import entries, PRIMARY, SPECIAL


def scripts(directory):
    for name, _ in PRIMARY + SPECIAL:
        (directory / name).touch()
    for name in ('trochanter_analysis_core.py', '_helper.py', 'rom_csv_nachauswertung.py', 'custom.py'):
        (directory / name).touch()


def test_normal_selection_only_exposes_primary_analyses(tmp_path):
    scripts(tmp_path)
    assert [p.name for _, p in entries(tmp_path)] == [name for name, _ in PRIMARY]


def test_special_and_custom_entries_remain_available_without_internal_helpers(tmp_path):
    scripts(tmp_path)
    paths = {p.name for _, p in entries(tmp_path, advanced=True)}
    assert paths == {name for name, _ in PRIMARY + SPECIAL} | {'custom.py'}


def test_selected_special_or_imported_script_remains_selected_when_collapsed(tmp_path):
    scripts(tmp_path)
    for name in ('custom.py', SPECIAL[1][0], 'rom_csv_nachauswertung.py'):
        paths = {p for _, p in entries(tmp_path, tmp_path/name)}
        assert (tmp_path/name).resolve() in paths
        assert len(paths) == len(PRIMARY)+1


def test_missing_entry_and_outside_selection_are_not_invented(tmp_path):
    assert entries(tmp_path, tmp_path.parent/'other.py') == []
