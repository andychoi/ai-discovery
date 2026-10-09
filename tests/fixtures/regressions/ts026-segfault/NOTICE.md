# Regression fixture: tree-sitter 0.26.0 segfault

`CatalogApi.cs` is copied unmodified from dotnet/eShop
(`src/Catalog.API/Apis/CatalogApi.cs`, commit dc7ea49), MIT License,
Copyright (c) .NET Foundation and Contributors.

Parsing it with `CSharpParser` under py-tree-sitter 0.26.0 segfaults
(QueryCursor.matches); 0.25.x is fine. Kept byte-identical so the crash
reproduces. Guarded by `tests/test_ts_floor.py::test_known_crash_fixtures_parse`.
