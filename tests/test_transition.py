"""自然过渡语系统测试 — 直接导入 core.quick_transition."""
import sys, random
sys.path.insert(0, '.')
from agent.core import quick_transition, _TRANSITION_TEMPLATES

TESTS = [
    ("你好",                None,      "短问候"),
    ("在吗亮哥",            None,      "短问候"),
    ("好的",                None,      "短确认"),
    ("帮我查一下Python的asyncio怎么用", "search", "搜索类"),
    ("帮我搜一下最近的AI新闻",          "search", "搜索类"),
    ("帮我找找那个文件在哪里",          "search", "搜索类"),
    ("为什么天空是蓝色的我一直想不明白",  "think",  "思考类"),
    ("这个东西用Python怎么实现比较好呢",  "think",  "思考类"),
    ("我这段代码有一个奇怪的bug帮我看看",   "code",   "代码类"),
    ("运行时报错了帮我看看怎么回事",  "code",   "代码类"),
    ("A" * 51,                          "long",   "长输入"),
    ("亮哥你好我想问一个事情",        "default","普通"),
    ("在吗亮哥帮我查个东西",          "search", "混合"),
]

def run_tests():
    passed, failed = 0, 0
    for text, expected_cat, desc in TESTS:
        result = quick_transition(text)
        if expected_cat is None:
            ok = result is None
        else:
            ok = result is not None and result in _TRANSITION_TEMPLATES.get(expected_cat, [])
        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL [{desc}]: input={text[:30]!r} expected={expected_cat} got={result!r}")
    return passed, failed

def test_diversity(samples=200):
    coverage = {cat: set() for cat in _TRANSITION_TEMPLATES}
    for seed in range(samples):
        random.seed(seed)
        for text, expected_cat, _ in TESTS:
            if expected_cat is None:
                continue
            result = quick_transition(text)
            if result and expected_cat in coverage:
                coverage[expected_cat].add(result)
    total = sum(len(v) for v in _TRANSITION_TEMPLATES.values())
    covered = sum(len(v) for v in coverage.values())
    return covered, total, coverage

if __name__ == "__main__":
    print("=" * 50)
    print("自然过渡语系统 — 测试报告")
    print("=" * 50)

    print("\n[1/2] 功能正确性")
    passed, failed = run_tests()
    print(f"  通过: {passed}/{len(TESTS)}")
    if failed:
        print(f"  失败: {failed}")

    print(f"\n[2/2] 模板覆盖 ({200}轮)")
    covered, total, cov = test_diversity(200)
    print(f"  覆盖: {covered}/{total}")
    for cat in ["search", "think", "code", "long", "default"]:
        c = cov.get(cat, set())
        print(f"    {cat}: {sorted(c)}")

    print("\n" + "=" * 50)
    if failed == 0 and covered == total:
        print("结果: PASS")
    else:
        print(f"结果: {failed} failures, {total - covered} uncovered")
