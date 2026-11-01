"""MyBatis mapper XML (roadmap item M6.9)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.extractors.mybatis import flatten, mybatis_edges, read_mapper_statements
from ai_discovery.extractors.sql_extractor import extract_table_access
from ai_discovery.parsers.java import JavaParser

MAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="shop.UserMapper">
  <!-- a comment with <select> inside must not count -->
  <sql id="cols">u.id, u.name</sql>
  <select id="search" resultType="map">
    SELECT <include refid="cols"/> FROM users u
    <where>
      <if test="name != null">AND u.name LIKE #{name}</if>
      <choose>
        <when test="vip">AND u.id IN (SELECT user_id FROM vip_members)</when>
        <otherwise>AND u.active = 1</otherwise>
      </choose>
      <if test="ids != null">AND u.id IN
        <foreach item="i" collection="ids" open="(" separator="," close=")">#{i}</foreach>
      </if>
    </where>
    <![CDATA[ AND u.age < 30 ]]>
  </select>
  <update id="rename">
    UPDATE users <set><if test="name != null">name = #{name},</if></set> WHERE id = #{id}
  </update>
  <delete id="purge">DELETE FROM ${tableName} WHERE created &lt; #{cutoff}</delete>
  <insert id="audit">INSERT INTO audit_log (user_id) SELECT id FROM users WHERE id = #{id}</insert>
  <select id="orphan">SELECT 1 FROM sessions</select>
</mapper>
"""


def _setup(tmp_path: Path) -> list:
    (tmp_path / "src/main/resources/mapper").mkdir(parents=True)
    (tmp_path / "src/main/resources/mapper/UserMapper.xml").write_text(MAPPER)
    (tmp_path / "src/main/java/shop").mkdir(parents=True)
    (tmp_path / "src/main/java/shop/UserMapper.java").write_text(
        "package shop;\npublic interface UserMapper {\n  java.util.List<Object> search(String name);\n"
        "  int rename(long id, String name);\n  int purge(String tableName);\n  int audit(long id);\n"
        "  @Select(\"SELECT id FROM users WHERE id = #{id}\")\n  Object byId(long id);\n}\n")
    return [n for f in (tmp_path / "src/main/java").rglob("*.java") for n in JavaParser().parse_file(f)]


def test_flattening_keeps_every_branch_and_marks_dynamic_names(tmp_path) -> None:
    _setup(tmp_path)
    st = {s.name: s for s in read_mapper_statements(tmp_path)}
    assert set(st) == {"search", "rename", "purge", "audit", "orphan"}
    search = st["search"].source_code
    assert "u.id, u.name FROM users u" in search and "vip_members" in search and "u.active = 1" in search
    assert "#{" not in search and "<" not in search.replace("u.age < 30", "")
    assert "__dyn__" in st["purge"].source_code and "&lt;" not in st["purge"].source_code
    assert flatten("UPDATE t <set><if test='a'>a = #{a},</if></set> WHERE id = 1", {}) == "UPDATE t SET a = ? WHERE id = 1"


def test_interface_methods_bind_to_statements_and_statements_to_tables(tmp_path) -> None:
    nodes = _setup(tmp_path)
    st = read_mapper_statements(tmp_path)
    _tables, edges = mybatis_edges(st, nodes)
    got = {(e.caller, e.callee, e.metadata["edge_kind"]) for e in edges}
    assert ("shop.UserMapper.search", "mybatis::shop.UserMapper.search", "framework_binds") in got
    assert ("mybatis::shop.UserMapper.search", "sql::users", "reads_table") in got
    assert ("mybatis::shop.UserMapper.search", "sql::vip_members", "reads_table") in got     # a <when> branch
    assert ("mybatis::shop.UserMapper.rename", "sql::users", "writes_table") in got
    assert ("mybatis::shop.UserMapper.audit", "sql::audit_log", "writes_table") in got
    assert ("mybatis::shop.UserMapper.audit", "sql::users", "reads_table") in got
    assert not [g for g in got if g[0] == "mybatis::shop.UserMapper.purge"]                  # ${tableName}
    assert not [g for g in got if g[1] == "mybatis::shop.UserMapper.orphan"]                 # no interface method


def test_annotation_mapper_sql_is_ordinary_table_access(tmp_path) -> None:
    nodes = _setup(tmp_path)
    _t, edges, _s = extract_table_access(nodes)
    assert ("shop.UserMapper.byId", "sql::users") in {(e.caller, e.callee) for e in edges}
