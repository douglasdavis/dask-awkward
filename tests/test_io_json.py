from __future__ import annotations

import os
from pathlib import Path

import awkward as ak
import dask
import pytest

import dask_awkward as dak
from dask_awkward.lib.core import Array
from dask_awkward.lib.optimize import optimize as dak_optimize
from dask_awkward.lib.testutils import assert_eq

try:
    import ujson as json
except ImportError:
    import json  # type: ignore[no-redef]


data1 = r"""{"name":"Bob","team":"tigers","goals":[0,0,0,1,2,0,1]}
{"name":"Alice","team":"bears","goals":[3,2,1,0,1]}
{"name":"Jack","team":"bears","goals":[0,0,0,0,0,0,0,0,1]}
{"name":"Jill","team":"bears","goals":[3,0,2]}
{"name":"Ted","team":"tigers","goals":[0,0,0,0,0]}
"""

data2 = r"""{"name":"Ellen","team":"tigers","goals":[1,0,0,0,2,0,1]}
{"name":"Dan","team":"bears","goals":[0,0,3,1,0,2,0,0]}
{"name":"Brad","team":"bears","goals":[0,0,4,0,0,1]}
{"name":"Nancy","team":"tigers","goals":[0,0,1,1,1,1,0]}
{"name":"Lance","team":"bears","goals":[1,1,1,1,1]}
"""

data3 = r"""{"name":"Sara","team":"tigers","goals":[0,1,0,2,0,3]}
{"name":"Ryan","team":"tigers","goals":[1,2,3,0,0,0,0]}
"""


@pytest.fixture(scope="session")
def json_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    toplevel = tmp_path_factory.mktemp("json_data")
    for i, datum in enumerate((data1, data2, data3)):
        with open(toplevel / f"file{i}.json", "w") as f:
            print(datum, file=f)
    return toplevel


@pytest.fixture(scope="session")
def concrete_data(json_data_dir: Path) -> ak.Array:
    array = ak.concatenate(
        [
            ak.from_json(json_data_dir / "file0.json", line_delimited=True),
            ak.from_json(json_data_dir / "file1.json", line_delimited=True),
            ak.from_json(json_data_dir / "file2.json", line_delimited=True),
        ],
    )

    return array


def test_json_sanity(json_data_dir: Path, concrete_data: ak.Array) -> None:
    ds = dak.from_json(str(json_data_dir) + "/*.json")
    assert ds

    assert_eq(ds, concrete_data)


def input_layer_array_partition0(collection: Array) -> ak.Array:
    """Get first partition concrete array after the input layer.

    Parameteters
    ------------
    collection : dask_awkward.Array
        dask-awkward Array collection of interest

    Returns
    -------
    ak.Array
        Concrete awkward array representing the first partition
        immediately after the input layer.

    """
    with dask.config.set({"awkward.optimization.which": ["columns"]}):
        optimized_hlg = dak_optimize(collection.dask, [])
        layer_name = [
            name for name in list(optimized_hlg.layers) if name.startswith("from-json")
        ][0]
        sgc, arg = optimized_hlg[(layer_name, 0)]
        array = sgc.dsk[layer_name][0](arg)
    return array


def test_json_column_projection1(json_data_dir: Path) -> None:
    ds = dak.from_json(str(json_data_dir) + "/*.json")
    ds2 = ds[["name", "goals"]]
    array = input_layer_array_partition0(ds2)
    assert array.fields == ["name", "goals"]


def test_json_column_projection2(json_data_dir: Path) -> None:
    ds = dak.from_json(str(json_data_dir) + "/*.json")
    # grab name and goals but then only use goals!
    ds2 = dak.max(ds[["name", "goals"]].goals, axis=1)
    array = input_layer_array_partition0(ds2)
    assert array.fields == ["goals"]


def test_json_force_by_lines_meta(ndjson_points_file: str) -> None:
    daa1 = dak.from_json(
        [ndjson_points_file] * 5,
        derive_meta_kwargs={"force_by_lines": True},
    )
    daa2 = dak.from_json([ndjson_points_file] * 3)
    assert daa1._meta is not None
    assert daa2._meta is not None
    f1 = daa1._meta.layout.form
    f2 = daa2._meta.layout.form
    assert f1 == f2


def test_derive_json_meta_trigger_warning(ndjson_points_file: str) -> None:
    with pytest.warns(UserWarning):
        dak.from_json([ndjson_points_file], derive_meta_kwargs={"bytechunks": 64})


def test_json_one_obj_per_file(single_record_file: str) -> None:
    daa = dak.from_json(
        [single_record_file] * 5,
        one_obj_per_file=True,
    )
    caa = ak.concatenate([ak.from_json(Path(single_record_file))] * 5)
    assert_eq(daa, caa)


def test_json_delim_defined(ndjson_points_file: str) -> None:
    source = [ndjson_points_file] * 6
    daa = dak.from_json(source, delimiter=b"\n")

    concretes = []
    for s in source:
        with open(s) as f:
            for line in f:
                concretes.append(json.loads(line))
    caa = ak.from_iter(concretes)
    assert_eq(
        daa["points"][["x", "y"]],
        caa["points"][["x", "y"]],
    )


def test_json_sample_rows_true(ndjson_points_file: str) -> None:
    source = [ndjson_points_file] * 5

    daa = dak.from_json(
        source,
        derive_meta_kwargs={"force_by_lines": True, "sample_rows": 2},
    )

    concretes = []
    for s in source:
        with open(s) as f:
            for line in f:
                concretes.append(json.loads(line))
    caa = ak.from_iter(concretes)

    assert_eq(daa, caa)


def test_json_bytes_no_delim_defined(ndjson_points_file: str) -> None:
    source = [ndjson_points_file] * 7
    daa = dak.from_json(source, blocksize=650, delimiter=None)

    concretes = []
    for s in source:
        with open(s) as f:
            for line in f:
                concretes.append(json.loads(line))

    caa = ak.from_iter(concretes)
    assert_eq(daa, caa)


@pytest.mark.parametrize("compression", ["xz", "gzip", "zip"])
def test_to_and_from_json(daa, tmpdir_factory, compression):
    tdir = str(tmpdir_factory.mktemp("json_temp"))

    p1 = os.path.join(tdir, "z", "z")

    dak.to_json(daa, p1, compute=True)
    paths = list((Path(tdir) / "z" / "z").glob("part*.json"))
    assert len(paths) == daa.npartitions
    arrays = ak.concatenate([ak.from_json(p, line_delimited=True) for p in paths])
    assert_eq(daa, arrays)

    x = dak.from_json(os.path.join(p1, "*.json"))
    assert_eq(arrays, x)

    s = dak.to_json(
        daa,
        tdir,
        compression=compression,
        compute=False,
    )
    s.compute()
    suffix = "gz" if compression == "gzip" else compression
    r = dak.from_json(os.path.join(tdir, f"*.json.{suffix}"))
    assert_eq(x, r)
