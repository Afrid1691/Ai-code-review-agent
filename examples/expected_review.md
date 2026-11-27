## Code Review — examples/sample_pr.diff

**Verdict: Request changes**

This change needs work before it merges: 2 critical, 3 high, 3 low finding(s) across the three reviewers. The most urgent item is "Hardcoded secret committed to source code" in app/config.py. Fix the critical and high severity items, address or reply to the rest, and the change should be in good shape for another pass.

**Findings:** 8 (2 critical, 3 high, 3 low)

### 1. [CRITICAL] Hardcoded secret committed to source code
*app/config.py:8 — reported by Security*

The API_KEY is assigned a literal value in the source. Anyone with repo access (and anyone the repo leaks to) now has the credential, and it lives in git history even after it is removed.

**Suggested fix:** Read the value from an environment variable or a secrets manager, and rotate the exposed credential.

### 2. [CRITICAL] SQL injection via string-formatted query
*app/users.py:20 — reported by Security*

The query is built by formatting user-controlled values directly into the SQL string. An attacker can break out of the quoted value and run arbitrary SQL.

**Suggested fix:** Use a parameterized query: cursor.execute("SELECT ... WHERE name = %s", (name,)) and let the database driver handle escaping.

### 3. [HIGH] Possible None dereference: return value used without a None check
*app/users.py:26 — reported by Bug Hunter*

`user` comes from a function that returns None when there is no result, and it is dereferenced on the next line with no `if user` guard. A missing record turns into an AttributeError at runtime.

**Suggested fix:** Check for None first: `if user is None: ...` and handle the missing case explicitly.

### 4. [HIGH] Bare except clause swallows all errors
*app/users.py:35 — reported by Bug Hunter*

A bare `except:` catches everything, including KeyboardInterrupt and real bugs, and the empty handler hides the failure completely. Callers see a normal return and have no way to tell something went wrong.

**Suggested fix:** Catch the specific exceptions you expect (for example ValueError or requests.RequestException), log the error, and re-raise or return an explicit failure value.

### 5. [HIGH] Dynamic code execution with eval()
*app/users.py:41 — reported by Security*

eval() and exec() run whatever string they are given as Python code, so any value that reaches them from outside the codebase becomes arbitrary code execution.

**Suggested fix:** Do not evaluate caller-supplied strings. Parse the input into a fixed set of operations instead (for example a mapping of named filters), or use ast.literal_eval for plain data.

### 6. [LOW] Public function 'find_users_by_name' has no docstring
*app/users.py:18 — reported by Style & Quality*

Public functions without a docstring force readers to reverse-engineer the contract from the body, which slows down every future change.

**Suggested fix:** Add a one-line docstring that says what the function returns and when it returns None or raises.

### 7. [LOW] Public function 'get_user_email' has no docstring
*app/users.py:24 — reported by Style & Quality*

Public functions without a docstring force readers to reverse-engineer the contract from the body, which slows down every future change.

**Suggested fix:** Add a one-line docstring that says what the function returns and when it returns None or raises.

### 8. [LOW] Debug print left in production code
*app/users.py:33 — reported by Style & Quality*

A print() call writes straight to stdout, bypassing the logging setup and making the output impossible to filter or silence in production.

**Suggested fix:** Use the module logger (logger.debug/info) instead.

