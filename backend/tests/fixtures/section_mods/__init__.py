"""load_sections 的测试替身包：结构同 app/modules（<子包>/sections.py）。

- alpha：导出 BUILDERS（推荐写法），section 名 ALPHA；
- beta：导出幂等的 register()（Palantir 现在的写法），section 名 BETA；
- gamma：没有 sections.py，跳过。

section 名一律用 `_test_` 前缀，**不能用真实 section**（terms、eventTypes 等）：
test_seed_fresh_db 会把本包和 app.modules 一起扫，真实模块（如静态的 terms）登记后再用同名就会冲突。
register_section 只认 SECTION_OWNERS 里的名字，所以这里在导入本包时把测试名补进去（只在测试进程里生效）。
"""

from app.contracts.versioning import SECTION_OWNERS

ALPHA = "_test_alpha"
BETA = "_test_beta"
TEST_SECTIONS = (ALPHA, BETA)

for _name in TEST_SECTIONS:
    SECTION_OWNERS.setdefault(_name, "tests")
