#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小萤架构绝对地图快照生成工具 (通用型)
无任何具体 Bug 的硬编码。纯粹通过 AST 提取 Python 源码结构，输出为极轻量的 JSON 架构缓存快照，
作为小萤大脑的“骨架地图”，用于大模型高精度自主审查、逻辑推演与问题清单智能对齐。
"""

import os
import ast
import json
import argparse
from pathlib import Path


def extract_file_ast(file_path: Path) -> dict:
    """使用 AST 提取单个 Python 文件的骨架结构（类、方法、独立函数、文档注释）"""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        
        docstring = ast.get_docstring(tree) or ""
        classes = []
        functions = []
        
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for n in ast.iter_child_nodes(node):
                    if isinstance(n, ast.FunctionDef):
                        arg_names = [arg.arg for arg in n.args.args]
                        methods.append({
                            "name": n.name,
                            "args": arg_names,
                            "line": n.lineno,
                            "doc": ast.get_docstring(n) or ""
                        })
                classes.append({
                    "name": node.name,
                    "methods": methods,
                    "line": node.lineno,
                    "doc": ast.get_docstring(node) or ""
                })
            elif isinstance(node, ast.FunctionDef):
                arg_names = [arg.arg for arg in node.args.args]
                functions.append({
                    "name": node.name,
                    "args": arg_names,
                    "line": node.lineno,
                    "doc": ast.get_docstring(node) or ""
                })
                
        return {
            "doc": docstring,
            "classes": classes,
            "functions": functions,
            "size_bytes": len(content)
        }
    except Exception as e:
        return {"error": str(e)}


def generate_project_map(source_dir: Path) -> dict:
    """递归扫描源目录，生成整个代码库的结构快照数据"""
    project_map = {}
    for p in source_dir.rglob("*.py"):
        if p.name.startswith("__") or ".stitch_env" in str(p) or ".venv" in str(p) or "venv" in str(p):
            continue
        rel_path = str(p.relative_to(source_dir.parent))
        project_map[rel_path] = extract_file_ast(p)
    return project_map


def main():
    parser = argparse.ArgumentParser(description="Little Ying Project Architecture Map Generator")
    parser.add_argument("--src", type=str, default="agent", help="Source directory to scan")
    parser.add_argument("--out", type=str, default="scratch/project_structure_cache.json", help="Output path for the json cache")
    args = parser.parse_args()

    src_path = Path(args.src)
    out_path = Path(args.out)

    if not src_path.is_absolute():
        src_path = Path.cwd() / src_path

    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path

    if not src_path.exists():
        print(f"Error: Source directory does not exist: {src_path}")
        return

    print(f"Scanning Python files in {src_path}...")
    project_map = generate_project_map(src_path)
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(project_map, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"Success! Project architecture map written to {out_path} ({len(project_map)} modules cached).")


if __name__ == "__main__":
    main()
