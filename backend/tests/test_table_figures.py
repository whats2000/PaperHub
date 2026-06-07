from paperhub.pipelines.table_figures import _is_hostile


def test_starred_and_x_envs_are_hostile() -> None:
    assert _is_hostile("tabular*", "a & b \\\\")
    assert _is_hostile("tabularx", "a & b \\\\")


def test_plain_tabular_is_not_hostile() -> None:
    assert not _is_hostile("tabular", "a & b & c \\\\ \\midrule x & 1 & 2 \\\\")


def test_multirow_or_makecell_makes_plain_tabular_hostile() -> None:
    assert _is_hostile("tabular", "\\multirow{2}{*}{a} & b \\\\")
    assert _is_hostile("tabular", "\\makecell{a\\\\b} & c \\\\")


def test_multicolumn_alone_is_not_hostile_but_with_cmidrule_is() -> None:
    assert not _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\")
    assert _is_hostile("tabular", "\\multicolumn{2}{c}{a} \\\\ \\cmidrule(lr){1-2}")


from paperhub.pipelines.table_figures import _find_table_envs


def test_finds_a_simple_tabular() -> None:
    tex = "before \\begin{tabular}{cc}a & b\\\\\\end{tabular} after"
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular"
    assert tex[start:end] == "\\begin{tabular}{cc}a & b\\\\\\end{tabular}"


def test_nested_tabular_inside_tabular_star_yields_one_outermost_env() -> None:
    tex = (
        "\\begin{tabular*}{\\textwidth}{cc}"
        "\\begin{tabular}{cc}x & y\\\\\\end{tabular}"
        " & z\\\\\\end{tabular*}"
    )
    envs = _find_table_envs(tex)
    assert len(envs) == 1
    start, end, name = envs[0]
    assert name == "tabular*"
    assert tex[start:end] == tex  # spans the whole outer tabular*


def test_two_sibling_tables_yield_two_envs() -> None:
    tex = (
        "\\begin{tabular}{c}a\\\\\\end{tabular}"
        "MID"
        "\\begin{tabularx}{\\linewidth}{c}b\\\\\\end{tabularx}"
    )
    envs = _find_table_envs(tex)
    assert [n for _, _, n in envs] == ["tabular", "tabularx"]


def test_unclosed_env_is_skipped() -> None:
    tex = "\\begin{tabular}{cc}a & b oops no end"
    assert _find_table_envs(tex) == []
