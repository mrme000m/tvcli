# Browser DevTools Debugging Benchmark v3.1

**Version:** 3.1  
**Date:** 2025-11-24

---

## Tools Under Test

- **bdg**: Browser Debugger CLI (`bdg` command) - This project's CLI tool for CDP automation
- **MCP**: Chrome DevTools MCP Server (https://github.com/ChromeDevTools/chrome-devtools-mcp)

Both tools are available to the testing agent.

---

## Test Order Randomization

**IMPORTANT:** Alternate which tool goes first to prevent learning bias.

| Test | Run 1 | Run 2 |
|------|-------|-------|
| Test 1 (Basic Error) | bdg first | MCP first |
| Test 2 (Multiple Errors) | MCP first | bdg first |
| Test 3 (SPA Debugging) | bdg first | MCP first |
| Test 4 (Form Validation) | MCP first | bdg first |
| Test 5 (Memory Leak) | bdg first | MCP first |

Use this alternating pattern for fair comparison across all 5 tests.

---

## Test 1: Basic Error (⭐ Easy)

**URL:** https://microsoftedge.github.io/Demos/devtools-explain-error/  
**Time Limit:** 2 minutes  
**Points:** 20

**Task:**
Find and diagnose ONE JavaScript error. Pick any "Run" button, click it, and analyze the error.

**Agent Instructions:**
```
URL: https://microsoftedge.github.io/Demos/devtools-explain-error/
Time: 2 minutes
Goal: Find and diagnose one JavaScript error
```

**Evaluation:**

| Criterion | Full (pts) | Partial (pts) |
|-----------|------------|---------------|
| Navigates and checks console | 2 | 1 |
| Triggers one error via button | 3 | 1 |
| Captures error message | 3 | 1 |
| Identifies error type | 2 | 1 |
| Locates code line | 2 | 1 |
| Explains why error occurs | 2 | 1 |
| Systematic approach | 3 | 1 |
| Clear findings | 3 | 1 |

**Time Penalty:** -2 pts per 30s over 2 min

---

## Test 2: Multiple Errors (⭐⭐ Moderate)

**URL:** https://microsoftedge.github.io/Demos/devtools-explain-error/  
**Time Limit:** 3 minutes  
**Points:** 20

**Task:**
Find 5+ JavaScript errors. Click all "Run" buttons systematically, categorize errors by type.

**Agent Instructions:**
```
URL: https://microsoftedge.github.io/Demos/devtools-explain-error/
Time: 3 minutes
Goal: Find and categorize 5+ JavaScript errors
```

**Evaluation:**

| Criterion | Full (pts) | Partial (pts) |
|-----------|------------|---------------|
| Sets up clean console | 2 | 1 |
| Clicks all buttons systematically | 3 | 1 |
| Captures 5+ errors | 3 | 1 |
| Categorizes by error type | 2 | 1 |
| Extracts stack traces | 2 | 1 |
| Maps errors to buttons | 2 | 1 |
| Methodical approach | 3 | 1 |
| Provides summary | 3 | 1 |

**Time Penalty:** -2 pts per 30s over 3 min

---

## Test 3: SPA Debugging (⭐⭐⭐ Advanced)

**URL:** https://demo.playwright.dev/todomvc  
**Time Limit:** 5 minutes  
**Points:** 20

**Task:**
Debug React TodoMVC app. Test edge cases, correlate console errors with network activity.

**Agent Instructions:**
```
URL: https://demo.playwright.dev/todomvc
Time: 5 minutes
Goal: Debug the React SPA - find console and network issues
```

**Evaluation:**

| Criterion | Full (pts) | Partial (pts) |
|-----------|------------|---------------|
| Tests edge cases | 3 | 1 |
| Checks console AND network | 3 | 1 |
| Captures warnings + errors | 2 | 1 |
| Links errors to actions | 2 | 1 |
| Correlates network with UI | 2 | 1 |
| Identifies error sources | 2 | 1 |
| Tests realistic scenarios | 2 | 1 |
| Multi-domain analysis | 2 | 1 |
| Clear reproduction steps | 2 | 1 |

**Time Penalty:** -2 pts per 60s over 5 min

---

## Test 4: Form Validation (⭐⭐⭐⭐ Expert)

**URL:** https://testpages.eviltester.com/styled/validation/input-validation.html  
**Time Limit:** 5 minutes  
**Points:** 20

**Task:**
Test all form fields with valid and invalid inputs. Find validation bugs.

**Agent Instructions:**
```
URL: https://testpages.eviltester.com/styled/validation/input-validation.html
Time: 5 minutes
Goal: Test form validation - find bugs in validation logic
```

**Evaluation:**

| Criterion | Full (pts) | Partial (pts) |
|-----------|------------|---------------|
| Tests valid inputs | 2 | 1 |
| Tests invalid inputs | 2 | 1 |
| Discovers edge cases | 2 | 1 |
| Checks console throughout | 2 | 1 |
| Finds logic errors | 3 | 1 |
| Distinguishes broken vs working | 2 | 1 |
| Provides test cases | 1 | 0 |
| Methodical approach | 2 | 1 |
| Tests all fields | 2 | 1 |
| Clear categorization | 2 | 1 |

**Time Penalty:** -2 pts per 60s over 5 min

---

## Test 5: Memory Leak (⭐⭐⭐⭐⭐ Master)

**URL:** https://microsoftedge.github.io/Demos/detached-elements/  
**Time Limit:** 8 minutes  
**Points:** 20

**Task:**
Detect and diagnose DOM memory leak. Add/remove messages, measure memory growth, identify leak source.

**Agent Instructions:**
```
URL: https://microsoftedge.github.io/Demos/detached-elements/
Time: 8 minutes
Goal: Detect and diagnose the memory leak using profiling tools
```

**Evaluation:**

| Criterion | Full (pts) | Partial (pts) |
|-----------|------------|---------------|
| Takes baseline measurement | 2 | 1 |
| Triggers leak via interactions | 2 | 1 |
| Detects memory growth | 2 | 1 |
| Uses profiling tools | 2 | 1 |
| Identifies what leaks | 2 | 1 |
| Quantifies leak | 2 | 1 |
| Explains why leak occurs | 2 | 1 |
| Baseline → trigger → measure | 2 | 1 |
| Uses appropriate tools | 2 | 1 |
| Actionable recommendations | 2 | 1 |

**Time Penalty:** -2 pts per 90s over 8 min

---

## Scoring Summary

**Total:** 100 points (20 per test)

**Per Test Breakdown:**
- Discovery: 8 pts
- Analysis: 6 pts  
- Workflow: 6 pts

**Token Efficiency Score (TES):**
```
TES = (Total Score × 100) / (Total Tokens / 1000)
```

**Minimum Score:** 0 (penalties cannot make score negative)

---

## Results Template

```markdown
# Benchmark v3.1 Results

**Date:** YYYY-MM-DD  
**Test Order:** [bdg→MCP or MCP→bdg]

## Summary

| Test | bdg Score | bdg Time | bdg Tokens | MCP Score | MCP Time | MCP Tokens |
|------|-----------|----------|------------|-----------|----------|------------|
| Test 1 | /20 | s | ~K | /20 | s | ~K |
| Test 2 | /20 | s | ~K | /20 | s | ~K |
| Test 3 | /20 | s | ~K | /20 | s | ~K |
| Test 4 | /20 | s | ~K | /20 | s | ~K |
| Test 5 | /20 | s | ~K | /20 | s | ~K |
| **TOTAL** | **/100** | | **~XK** | **/100** | | **~XK** |

**Token Efficiency:**
- bdg TES: X.X
- MCP TES: X.X

**Winner:** [tool name] (X point advantage, X.Xx TES advantage)

---

## Test 1: Basic Error

### bdg
**Score:** X/20  
**Time:** Xs  
**Tokens:** ~X,XXX

**What Happened:**
[Brief description]

**Discovery (X/8):**
- [scoring breakdown]

**Analysis (X/6):**
- [scoring breakdown]

**Workflow (X/6):**
- [scoring breakdown]

### MCP
**Score:** X/20  
**Time:** Xs  
**Tokens:** ~X,XXX

**What Happened:**
[Brief description]

**Discovery (X/8):**
- [scoring breakdown]

**Analysis (X/6):**
- [scoring breakdown]

**Workflow (X/6):**
- [scoring breakdown]

---

[Repeat for Tests 2-5]

---

## Overall Assessment

**bdg Strengths:**
- [what it did well]

**bdg Weaknesses:**
- [what it struggled with]

**MCP Strengths:**
- [what it did well]

**MCP Weaknesses:**
- [what it struggled with]

**Recommendation:**
- Use bdg for: [scenarios]
- Use MCP for: [scenarios]
```

---

## Execution Checklist

Before running:

- [ ] URLs accessible
- [ ] Browser in headed mode (not headless)
- [ ] Fresh browser profile
- [ ] Timer ready
- [ ] Token counter ready
- [ ] Determined test order (bdg first or MCP first)

During execution:

- [ ] Provide only URL, time, and goal to agent
- [ ] No hints about expected errors
- [ ] No step-by-step instructions
- [ ] No command suggestions
- [ ] Start timer when URL provided
- [ ] Count only tool output tokens

After completion:

- [ ] Score using criteria tables
- [ ] Apply time penalties
- [ ] Calculate TES
- [ ] Document findings
- [ ] Swap tool order for next run
