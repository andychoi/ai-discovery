# Encoding fixtures (roadmap M0.4)

UTF-8 sources with Korean comments and string literals inside class bodies.
`tests/test_encoding.py` writes CP949, EUC-KR, UTF-16 and UTF-8-BOM twins of
these files at test time and asserts every twin parses to the same nodes as
the UTF-8 original, with Hangul preserved. Keep these files UTF-8.
