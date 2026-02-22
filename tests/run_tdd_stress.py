#!/usr/bin/env python3
"""TDD Pipeline Stress Test — designed to trigger debug loops.

Complex mdtable task with strict black-box scenarios, edge cases,
and tricky parsing requirements to stress-test the debug subsystems.

Usage:
    python tests/run_tdd_stress.py [--keep] [--model MODEL]
"""

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from toyshop import create_toyshop_llm
from toyshop.tdd_pipeline import run_tdd_pipeline, TDDResult


# A deliberately tricky requirement: state machine with edge cases
REQUIREMENTS = """\
创建一个 Markdown 表格解析与查询引擎 (mdtable)。

## 功能需求

### 1. 解析器 (Parser)
- 解析标准 Markdown 表格文本为结构化数据
- 支持对齐标记 (`:---`, `:---:`, `---:`) 识别左/中/右对齐
- 处理转义管道符 `\\|` (不作为分隔符)
- 处理单元格内的行内代码 `` `code` `` (其中的 `|` 不分割)
- 空单元格保留为空字符串
- 自动 trim 每个单元格的前后空白

### 2. 查询引擎 (Query)
- `select(columns)` — 选择指定列，返回新表
- `where(column, op, value)` — 过滤行，op 支持: ==, !=, >, <, >=, <=, contains, startswith
  - 数值列自动转换为 float 进行比较
  - 字符串比较区分大小写
- `order_by(column, reverse=False)` — 排序，数值列按数值排序，字符串按字典序
- `limit(n)` — 取前 n 行
- 支持链式调用: `table.select(...).where(...).order_by(...).limit(5)`

### 3. 输出 (Renderer)
- `to_markdown()` — 输出为标准 Markdown 表格字符串
- `to_csv()` — 输出为 CSV 格式字符串
- `to_dict_list()` — 输出为 list[dict] 格式

### 4. 统计 (Aggregator)
- `count()` — 行数
- `sum(column)` — 数值列求和，非数值抛 ValueError
- `avg(column)` — 数值列平均值
- `min_val(column)` / `max_val(column)` — 最小/最大值
- `group_by(column)` — 按列分组，返回 dict[str, Table]

## 边界情况（必须正确处理）
- 表头行和分隔行之间不能有空行
- 只有表头没有数据行 -> 空表
- 列数不一致的行 -> 用空字符串补齐到表头列数，多余列截断
- 完全空的输入 -> 抛 ValueError("empty input")
- 没有分隔行的输入 -> 抛 ValueError("missing separator")
- where 中引用不存在的列 -> 抛 KeyError
- 数值比较时非数值内容 -> 该行视为不匹配（不报错，静默跳过）
"""


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def setup_workspace(workspace: Path):
    """Pre-populate openspec/ with design docs for the mdtable project."""
    openspec = workspace / "openspec"
    openspec.mkdir(parents=True, exist_ok=True)

    # design.md with interfaces
    (openspec / "design.md").write_text(DESIGN_MD, encoding="utf-8")
    # spec.md with test scenarios
    (openspec / "spec.md").write_text(SPEC_MD, encoding="utf-8")
    # proposal.md
    (openspec / "proposal.md").write_text(
        "# mdtable\nMarkdown表格解析与查询引擎。\n", encoding="utf-8"
    )
    # tasks.md
    (openspec / "tasks.md").write_text(
        "# Tasks\n1. 实现Parser\n2. 实现Query\n3. 实现Renderer\n4. 实现Aggregator\n",
        encoding="utf-8",
    )

    # Create module directory
    (workspace / "mdtable").mkdir(parents=True, exist_ok=True)
    (workspace / "mdtable" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "tests").mkdir(parents=True, exist_ok=True)
    (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")


# ── Design document with tricky interfaces ──
DESIGN_MD = r"""# mdtable Design

## Module: mdtable.parser

### Class: `Table`

Represents a parsed Markdown table.

#### Attributes
- `headers: list[str]` — column names
- `alignments: list[str]` — "left", "center", "right", or "default" per column
- `rows: list[list[str]]` — data rows (each row is list of cell strings)

#### Methods

`def __init__(self, headers: list[str], rows: list[list[str]], alignments: list[str] | None = None) -> None`

`def __len__(self) -> int`
Returns number of data rows.

`def __repr__(self) -> str`

`def column(self, name: str) -> list[str]`
Return all values in a column by name. Raises KeyError if not found.

`def column_index(self, name: str) -> int`
Return the index of a column by name. Raises KeyError if not found.

### Function: `parse`

`def parse(text: str) -> Table`

Parse a Markdown table string into a Table object.

Rules:
- First non-empty line is the header row
- Second line must be the separator row (contains only `|`, `-`, `:`, spaces)
- Remaining lines are data rows
- Escaped pipes `\|` inside cells are treated as literal `|`
- Pipes inside inline code backticks are not separators
- Each cell is stripped of leading/trailing whitespace
- If a data row has fewer columns than headers, pad with empty strings
- If a data row has more columns than headers, truncate to header count
- Empty input raises ValueError("empty input")
- Missing separator raises ValueError("missing separator")

## Module: mdtable.query

### Class: `QueryBuilder`

`def __init__(self, table: Table) -> None`

`def select(self, columns: list[str]) -> QueryBuilder`
Select specific columns. Raises KeyError for unknown columns.

`def where(self, column: str, op: str, value: str) -> QueryBuilder`
Filter rows. Supported ops: ==, !=, >, <, >=, <=, contains, startswith.
For >, <, >=, <=: attempt float conversion; rows where conversion fails are excluded silently.
Raises KeyError for unknown column.
Raises ValueError for unsupported op.

`def order_by(self, column: str, reverse: bool = False) -> QueryBuilder`
Sort rows. Numeric columns sorted numerically, others lexicographically.
Raises KeyError for unknown column.

`def limit(self, n: int) -> QueryBuilder`
Take first n rows.

`def execute(self) -> Table`
Apply all chained operations and return a new Table.

## Module: mdtable.render

`def to_markdown(table: Table) -> str`
Render table as Markdown string with proper alignment markers.

`def to_csv(table: Table) -> str`
Render table as CSV string (using comma separator, with proper quoting).

`def to_dict_list(table: Table) -> list[dict[str, str]]`
Convert table to list of dicts (one dict per row, keys are headers).

## Module: mdtable.aggregate

`def count(table: Table) -> int`
Return number of rows.

`def sum_col(table: Table, column: str) -> float`
Sum a numeric column. Raises ValueError if any value is not numeric.

`def avg_col(table: Table, column: str) -> float`
Average of a numeric column. Raises ValueError if not numeric or empty.

`def min_val(table: Table, column: str) -> str`
Minimum value in column (as string). Numeric columns compared numerically.

`def max_val(table: Table, column: str) -> str`
Maximum value in column (as string). Numeric columns compared numerically.

`def group_by(table: Table, column: str) -> dict[str, Table]`
Group rows by column value. Returns dict mapping value -> Table.
"""

SPEC_MD = r"""# mdtable Test Scenarios

## ── Parser: Basic ──

## Scenario: TC001 - Basic table parsing
Given a simple markdown table:
```
| Name  | Age | City    |
|-------|-----|---------|
| Alice | 30  | Beijing |
| Bob   | 25  | Shanghai|
```
When parsed with `parse(text)`
Then headers are ["Name", "Age", "City"]
And row count is 2
And rows[0] == ["Alice", "30", "Beijing"]
And rows[1][2] == "Shanghai" (trailing whitespace stripped)

## Scenario: TC002 - Alignment detection
Given a table with alignment markers:
```
| Left | Center | Right | Default |
|:-----|:------:|------:|---------|
| a    | b      | c     | d       |
```
When parsed
Then alignments are ["left", "center", "right", "default"]

## Scenario: TC003 - Escaped pipe in cell
Given a table with escaped pipe:
```
| Expression   | Result |
|--------------|--------|
| a \| b       | true   |
| x \| y \| z  | false  |
```
When parsed
Then rows[0][0] == "a | b"
And rows[1][0] == "x | y | z"
Note: the backslash is consumed, only literal pipe remains

## Scenario: TC004 - Inline code with pipe
Given a table with inline code containing pipe:
```
| Code            | Desc   |
|-----------------|--------|
| `a | b`         | or op  |
| `x | y | z`     | multi  |
| normal | cell   | plain  |
```
When parsed
Then rows[0][0] == "`a | b`" (pipe inside backticks preserved)
And rows[1][0] == "`x | y | z`"
And rows[2] == ["normal", "cell", "plain"]

## Scenario: TC005 - Empty cells
Given a table with empty cells:
```
| A | B | C |
|---|---|---|
| 1 |   | 3 |
|   | 2 |   |
```
When parsed
Then rows[0] == ["1", "", "3"]
And rows[1] == ["", "2", ""]

## Scenario: TC006 - Column count mismatch (pad and truncate)
Given a table where data rows have different column counts:
```
| A | B | C |
|---|---|---|
| 1 | 2 |
| 1 | 2 | 3 | 4 |
| only_one |
```
When parsed
Then rows[0] == ["1", "2", ""] (padded with empty string)
And rows[1] == ["1", "2", "3"] (extra column "4" truncated)
And rows[2] == ["only_one", "", ""] (heavily padded)

## Scenario: TC007 - Empty input
Given empty string ""
When parsed
Then raises ValueError with message containing "empty input"

## Scenario: TC008 - Missing separator
Given text without separator line:
```
| A | B |
| 1 | 2 |
```
When parsed
Then raises ValueError with message containing "missing separator"

## Scenario: TC019 - Header only table (no data rows)
Given a table with only header and separator:
```
| A | B |
|---|---|
```
When parsed
Then row count is 0 and headers are ["A", "B"]

## Scenario: TC020 - Leading/trailing whitespace in input
Given a table with blank lines before and after:
```

| X | Y |
|---|---|
| 1 | 2 |

```
When parsed
Then headers are ["X", "Y"] and rows[0] == ["1", "2"]
Note: leading/trailing blank lines should be ignored

## Scenario: TC021 - No leading/trailing pipes
Given a table without outer pipes:
```
 Name | Age
------|----
Alice | 30
Bob   | 25
```
When parsed
Then headers are ["Name", "Age"]
And rows[0] == ["Alice", "30"]

## Scenario: TC022 - Mixed escaped pipe and inline code
Given a table combining both escape mechanisms:
```
| Expr         | Code       | Note   |
|--------------|------------|--------|
| a \| b       | `c | d`    | mixed  |
```
When parsed
Then rows[0][0] == "a | b" (escaped)
And rows[0][1] == "`c | d`" (code span)

## ── Query Engine ──

## Scenario: TC009 - Select columns
Given a parsed table with columns Name, Age, City (3 rows)
When `QueryBuilder(table).select(["Name", "City"]).execute()`
Then result has only columns ["Name", "City"]
And result has same number of rows
And each row has exactly 2 elements

## Scenario: TC010 - Where with numeric comparison
Given a parsed table:
```
| Name    | Age | City     |
|---------|-----|----------|
| Alice   | 30  | Beijing  |
| Bob     | 25  | Shanghai |
| Charlie | 35  | Shenzhen |
```
When `QueryBuilder(table).where("Age", ">", "28").execute()`
Then result has 2 rows: Alice (30) and Charlie (35)

## Scenario: TC011 - Where with contains
Given the same table as TC010
When `QueryBuilder(table).where("City", "contains", "jing").execute()`
Then result has 1 row: Alice (Beijing)

## Scenario: TC023 - Where with startswith
Given the same table as TC010
When `QueryBuilder(table).where("City", "startswith", "Sh").execute()`
Then result has 2 rows: Bob (Shanghai) and Charlie (Shenzhen)

## Scenario: TC012 - Order by numeric
Given a parsed table with Age column containing "30", "25", "35"
When `QueryBuilder(table).order_by("Age").execute()`
Then rows are sorted by Age numerically ascending: 25, 30, 35

## Scenario: TC024 - Order by string (lexicographic)
Given a parsed table with Name column
When `QueryBuilder(table).order_by("Name").execute()`
Then rows are sorted alphabetically: Alice, Bob, Charlie

## Scenario: TC013 - Chain operations (select + where + order + limit)
Given a parsed table with Name, Age, City (Alice=30, Bob=25, Charlie=35, Diana=28)
When `QueryBuilder(table).select(["Name","Age"]).where("Age",">","25").order_by("Age",reverse=True).limit(2).execute()`
Then result has 2 rows: Charlie (35), Alice (30) — oldest two over 25

## Scenario: TC025 - Where with == on string
Given a parsed table
When `QueryBuilder(table).where("Name", "==", "Bob").execute()`
Then result has exactly 1 row with Name == "Bob"

## Scenario: TC026 - Where with != operator
Given a parsed table with 3 rows
When `QueryBuilder(table).where("Name", "!=", "Bob").execute()`
Then result has 2 rows (all except Bob)

## Scenario: TC027 - Where with <= and >=
Given a table with Age values 25, 30, 35
When `QueryBuilder(table).where("Age", ">=", "30").execute()`
Then result has 2 rows (Age 30 and 35)
When `QueryBuilder(table).where("Age", "<=", "30").execute()`
Then result has 2 rows (Age 25 and 30)

## Scenario: TC028 - Unsupported operator raises ValueError
Given a parsed table
When `QueryBuilder(table).where("Age", "~=", "30").execute()`
Then raises ValueError (unsupported operator)

## Scenario: TC018 - Where on non-existent column raises KeyError
Given a parsed table
When `QueryBuilder(table).where("NonExistent", "==", "x").execute()`
Then raises KeyError

## Scenario: TC029 - Select non-existent column raises KeyError
Given a parsed table
When `QueryBuilder(table).select(["Name", "Nonexistent"]).execute()`
Then raises KeyError

## Scenario: TC030 - Numeric silent skip in where
Given a table with mixed numeric/non-numeric Age column: "30", "N/A", "25", "unknown"
When `QueryBuilder(table).where("Age", ">", "20").execute()`
Then result contains only rows where Age is numeric and > 20 (skips "N/A" and "unknown" silently)
Result has exactly 2 rows

## Scenario: TC031 - Limit with 0
Given a parsed table with 3 rows
When `QueryBuilder(table).limit(0).execute()`
Then result has 0 rows

## Scenario: TC032 - Limit larger than row count
Given a parsed table with 3 rows
When `QueryBuilder(table).limit(100).execute()`
Then result has 3 rows (all rows, no error)

## Scenario: TC033 - Empty result after where
Given a parsed table
When `QueryBuilder(table).where("Age", ">", "999").execute()`
Then result has 0 rows, headers preserved

## ── Renderer ──

## Scenario: TC014 - to_markdown round-trip
Given a parsed table with alignment markers
When rendered with `to_markdown(table)` and re-parsed with `parse(result)`
Then the re-parsed table has same headers, rows, and alignments as original

## Scenario: TC015 - to_csv output
Given a parsed table:
```
| Name  | Age | City    |
|-------|-----|---------|
| Alice | 30  | Beijing |
```
When rendered with `to_csv(table)`
Then output is:
```
Name,Age,City
Alice,30,Beijing
```
(standard CSV format with header row)

## Scenario: TC034 - to_csv with comma in cell
Given a table where a cell contains a comma:
```
| Name          | Note       |
|---------------|------------|
| Smith, John   | test       |
```
When rendered with `to_csv(table)`
Then the cell "Smith, John" is properly quoted: `"Smith, John"`

## Scenario: TC035 - to_csv with quotes in cell
Given a table where a cell contains double quotes:
```
| Name  | Quote          |
|-------|----------------|
| Alice | She said "hi"  |
```
When rendered with `to_csv(table)`
Then the quotes are escaped per CSV standard (doubled): She said ""hi""

## Scenario: TC036 - to_dict_list
Given a parsed table with 2 rows
When `to_dict_list(table)` is called
Then result is a list of 2 dicts
And each dict has keys matching headers
And values match cell contents

## Scenario: TC037 - to_markdown preserves alignment
Given a table with alignments ["left", "center", "right"]
When rendered with `to_markdown(table)`
Then separator line contains `:---`, `:---:`, `---:` respectively

## ── Aggregator ──

## Scenario: TC016 - group_by
Given a table:
```
| Name  | Dept  | Salary |
|-------|-------|--------|
| Alice | Eng   | 100    |
| Bob   | Sales | 80     |
| Carol | Eng   | 120    |
```
When `group_by(table, "Dept")`
Then result has 2 groups
And "Eng" group has 2 rows (Alice, Carol)
And "Sales" group has 1 row (Bob)
And each group is a Table with same headers as original

## Scenario: TC017 - sum_col with non-numeric raises ValueError
Given a table with a Name column (non-numeric values)
When `sum_col(table, "Name")`
Then raises ValueError

## Scenario: TC038 - sum_col with numeric column
Given the table from TC016
When `sum_col(table, "Salary")`
Then result is 300.0

## Scenario: TC039 - avg_col
Given the table from TC016
When `avg_col(table, "Salary")`
Then result is 100.0

## Scenario: TC040 - min_val and max_val numeric
Given the table from TC016
When `min_val(table, "Salary")` -> "80"
And `max_val(table, "Salary")` -> "120"
Note: returns string representation, but comparison is numeric

## Scenario: TC041 - min_val and max_val string
Given the table from TC016
When `min_val(table, "Name")` -> "Alice" (lexicographic)
And `max_val(table, "Name")` -> "Carol"

## Scenario: TC042 - count
Given the table from TC016
When `count(table)`
Then result is 3

## Scenario: TC043 - avg_col on empty table raises ValueError
Given a table with headers but 0 rows
When `avg_col(table, "Salary")`
Then raises ValueError (cannot average empty column)

## Scenario: TC044 - group_by on non-existent column raises KeyError
Given a parsed table
When `group_by(table, "NonExistent")`
Then raises KeyError

## ── Integration / Complex ──

## Scenario: TC045 - Full pipeline: parse -> query -> render
Given markdown text:
```
| Product | Price | Stock |
|---------|------:|------:|
| Apple   | 3.5   | 100   |
| Banana  | 1.2   | 200   |
| Cherry  | 8.0   | 50    |
| Date    | 12.5  | 30    |
```
When parsed, then queried: select Price,Product where Price > 3 order_by Price desc limit 2
Then result has 2 rows: Date (12.5), Cherry (8.0)
When rendered to_csv, output is valid CSV with those 2 rows

## Scenario: TC046 - Query preserves original table immutability
Given a parsed table with 3 rows
When a query filters to 1 row
Then the original table still has 3 rows (query returns new Table, does not mutate)

## Scenario: TC047 - Multiple where clauses (chained)
Given a table with Name, Age, City
When `QueryBuilder(table).where("Age", ">", "20").where("City", "contains", "jing").execute()`
Then both conditions are applied (AND logic)

## Scenario: TC048 - group_by then aggregate each group
Given the table from TC016
When grouped by Dept, then sum_col("Salary") on each group
Then Eng sum is 220.0, Sales sum is 80.0
"""


def main():
    parser = argparse.ArgumentParser(description="TDD Stress Test - complex mdtable task")
    parser.add_argument("--keep", action="store_true", help="Keep workspace")
    parser.add_argument(
        "--model", type=str, default="openai/glm-5",
        help="Model for all agents (default: openai/glm-5)",
    )
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="mdtable_stress_"))
    print(f"Workspace: {workspace}")
    print(f"Model: {args.model}")

    try:
        # Setup workspace with pre-written design docs
        setup_workspace(workspace)
        print("Pre-populated openspec/ with design docs")

        # Create LLM
        llm = create_toyshop_llm(model=args.model)
        print(f"LLM ready: {llm.model}")

        # Run TDD pipeline (skip design phase — docs already written)
        print_section("TDD Pipeline (stress test)")
        start = datetime.now()
        result = run_tdd_pipeline(
            workspace=str(workspace),
            llm=llm,
            language="python",
        )
        elapsed = (datetime.now() - start).total_seconds()

        # Report
        print_section("RESULT")
        print(f"Success: {result.success}")
        print(f"White-box: {'PASSED' if result.whitebox_passed else 'FAILED'}")
        print(f"Black-box: {'PASSED' if result.blackbox_passed else 'FAILED'}")
        print(f"Retries: {result.retry_count}")
        print(f"Debug reports: {len(result.debug_reports)}")
        print(f"Legacy issues: {len(result.legacy_issues)}")
        print(f"Time: {elapsed:.1f}s")

        # Debug details
        for i, dr in enumerate(result.debug_reports):
            print(f"\nDebug Report #{i+1}:")
            print(f"  Failing tests: {dr.failing_tests}")
            for h in dr.hypotheses:
                print(f"  [{h.status}] {h.id}: {h.description[:100]}")
            for h in dr.excluded_hypotheses:
                print(f"  [excluded] {h.id}: {h.description[:100]}")

        for issue in result.legacy_issues:
            print(f"\nLegacy: {issue.test_name} — {issue.final_status}")

        print(f"\nSummary: {result.summary}")
        return 0 if result.success else 1

    finally:
        if not args.keep:
            print(f"\nCleaning up: {workspace}")
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            print(f"\nWorkspace preserved: {workspace}")


if __name__ == "__main__":
    sys.exit(main())