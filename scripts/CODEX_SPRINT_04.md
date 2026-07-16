# Sprint 04

## Mission

Implement the Connector Framework.

CareerAgent AI must never depend on a single job website.

Every platform must be replaceable by a plugin.

---

## Required architecture

Connector

↓

LinkedIn Connector

Indeed Connector

StepStone Connector

Xing Connector

Greenhouse Connector

Lever Connector

Workday Connector

SmartRecruiters Connector

Custom Company Connector

---

Every connector implements exactly the same interface.

class JobConnector

connect()

search_jobs()

get_job()

apply()

health()

---

The AgentBrain MUST NOT know which platform it uses.

It only calls

search_jobs()

The connector handles everything else.

---

Every connector returns normalized objects.

Job

Company

Salary

Location

Remote

Skills

Benefits

Application URL

Company Score

---

If one website changes,

only one connector changes.

Everything else continues working.

---

Requirements

• Clean Architecture

• SOLID

• Dependency Injection

• Typed Python

• pytest

• mypy

• Documentation

• README update

• 100% deterministic tests
