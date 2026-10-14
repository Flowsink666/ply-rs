# ply-rs

基于 Rust 原生内核的高性能现代 Python LALR(1) 词法与语法分析器生成器。

`ply-rs` 是经典项目 PLY 的现代继任者。它在保留纯 Python 灵活定义、正则语义与用户回调的前提下，将核心计算（词法分词、LALR 构表、LR 状态机解析）下沉至 Rust 原生层，大幅提升了执行性能与构表效率。

分发包名称为 `ply-rs`，在 Python 中直接 `import ply_rs` 即可使用。

---

## 核心特性

- **现代声明式 API**：提供基于类和装饰器（`@token`、`@rule`）的现代接口，定义清晰，语法与类型友好。
- **平滑生态兼容**：完全兼容 PLY (`rply.compat.ply`) 与 SLY (`rply.compat.sly`)，几乎零成本迁移。
- **Rust 原生加速**：词法扫描、LALR(1) 分析表生成和 LR 分析器状态机均基于 Rust 实现（通过 PyO3 绑定），消解大规模文法构表与解析开销。
- **严格保持 Python 正则语义**：词法引擎优先使用 Rust 原生正则多模式匹配；对复杂或特殊的 Python 正则特性，自动回退到 Python `re` 匹配单规则，速度与兼容性兼得。
- **分析表冻结（Freeze）**：支持将已计算好的分析表固化为只读快照（`frozen=True`），避免重复构表与校验，显著提升执行吞吐。
- **Python 原生语义动作**：语法的归约动作、AST 构造以及错误恢复（`errok()`、`restart()`）均直接在 Python 中编写与执行。

---

- **分层解耦**：用户通过高层 Python API 声明文法规则，Python 适配层将结构化定义传递至 Rust 扩展模块 `rply._core_rs`。
- **原生高速核心**：Rust 负责词法分词切片、LALR(1) 分析表计算与 LR 状态机推进，数据通过原生内存高效流转。
- **按需双向协作**：遇到复杂正则特性时，Rust 词法引擎按需单规则调用 Python `re`；语法分析器在归约和捕获语法错误时，直接触发用户的 Python 语义动作与错误恢复。

---

## 快速上手

### 1. 安装

运行环境要求 **Python 3.9+**。

从 PyPI 安装：

```sh
pip install rply-next
```

或者从源码构建安装（需本地安装 Rust 工具链）：

```sh
pip install maturin
maturin develop --release
```

### 2. Modern API 基础示例

以四则运算计算器为例（完整示例见 [examples/calculator.py](examples/calculator.py)）：

```python
from rply import Lexer, Parser, token, rule

# 1. 定义词法分析器
class CalculatorLexer(Lexer):
    ignore = " \t"
    literals = {"(", ")"}
    PLUS = r"\+"
    TIMES = r"\*"

    @token(r"\d+")
    def NUMBER(self, tok):
        tok.value = int(tok.value)
        return tok

# 2. 定义语法分析器
class CalculatorParser(Parser):
    tokens = CalculatorLexer.tokens
    precedence = [
        ("left", ["PLUS"]),
        ("left", ["TIMES"]),
    ]

    @rule("expr : expr PLUS expr")
    def add(self, p):
        return p[1] + p[3]

    @rule("expr : expr TIMES expr")
    def multiply(self, p):
        return p[1] * p[3]

    @rule("expr : '(' expr ')'")
    def group(self, p):
        return p[2]

    @rule("expr : NUMBER")
    def number(self, p):
        return p[1]

# 3. 运行解析
lexer = CalculatorLexer()
parser = CalculatorParser()
result = parser.parse(lexer.tokenize("(2 + 3) * 4"))
print(result)  # 输出 20
```

---

## 使用指南

### 1. 现代 API（Modern API）

#### 词法规则与优先级
- 类属性直接定义简单正则，例如 `PLUS = r"\+"`。
- 需要转换值或附加逻辑时使用 `@token(pattern, priority=N)` 装饰器。
- 优先级数值较大的规则先匹配；相同优先级按声明顺序匹配。
- 支持定义状态：`states = (("name", "inclusive"), ("other", "exclusive"))`，并可通过 `ignore_<state>` 指定特定状态下的忽略字符。

#### 语法规则与操作符优先级
- 规则定义支持 `:` 或 `::=`，例如 `@rule("expr : expr PLUS expr")`。
- 多条规则可合并在同一个规则字符串中，以 `|` 分隔。
- 操作符优先级列表格式为 `precedence = [("left", ["PLUS", "MINUS"]), ("right", ["UMINUS"])]`。

#### 语义动作传参
- **代理模式（默认）**：回调接收单个参数 `p`，通过 `p[1]` 索引或 `p.NUMBER` 符号名访问右部各符号值。
- **直接传值模式**：声明 `@rule(..., action_style="values")`，语义动作函数将按形参直接接收右部展开的值（例如 `def add(self, left, op, right): return left + right`）。

#### 分析表冻结（Freeze）优化
当语法规则确定不再动态变更时，建议开启冻结模式：

```python
# 方式一：类定义中声明
class MyParser(Parser):
    frozen = True
    ...

# 方式二：实例化时指定或手动调用
parser = MyParser(frozen=True)
# 或 parser.freeze()
```

冻结后，分析器将生成完全独立的只读分析表快照，跳过后续调用时的动态修改监听与全量比对，执行速度更快。

---

### 2. 兼容 PLY 与 SLY

已有项目可直接替换导入路径平滑迁移：

#### 兼容 PLY
将原先的 `ply.lex` 与 `ply.yacc` 替换为：

```python
from rply.compat.ply import lex, yacc

# 原有 t_* 规则、p_* 函数及 docstring 保持原样即可直接运行
lexer = lex.lex()
parser = yacc.yacc()
```

#### 兼容 SLY
将原先的 `sly` 替换为：

```python
from rply.compat.sly import Lexer, Parser, _

class CalcLexer(Lexer):
    ...

class CalcParser(Parser):
    @_("expr PLUS expr")
    def expr(self, p):
        return p.expr0 + p.expr1
```

支持 `@_` 装饰器、属性代理访问及面向对象类继承。

---

### 3. 核心层底层控制（Core API）

如果需要构建定制语言工具链或获取分析诊断信息，可以直接调用核心层接口：

- `Grammar`：编程式注册产生式、操作符优先级与开始符号。
- `LRTable`：生成 LALR(1) 分析表，支持检查 `conflicts` 与冲突项明细 `ConflictRecord`。
- `DiagnosticsFormatter`：导出易读的文法分析报告与冲突诊断。
- `LRParser` 与 `LexerEngine`：底层的独立分析器引擎驱动。

```python
from rply import Grammar, LRTable, LRParser, LexerEngine
```

---


## 开源协议

本项目采用 [MIT 许可证](LICENSE)。
