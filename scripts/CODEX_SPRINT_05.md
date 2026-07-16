# Sprint 05

## Mission

Implement the Employer Intelligence Engine.

This is one of the core competitive advantages of CareerAgent AI.

The system must evaluate employers instead of blindly applying to them.

---

## Components

Employer Intelligence Engine

↓

Review Collector

↓

Salary Analyzer

↓

Culture Analyzer

↓

Layoff Analyzer

↓

Hiring Speed Analyzer

↓

Scam Detector

↓

Employer Score Calculator

---

Each analyzer is an independent service.

The Engine combines all scores.

---

Employer Score

0–100

Factors

• Salary
• Reviews
• Layoffs
• Interview Experience
• Remote Policy
• Benefits
• Work-Life Balance
• Career Growth
• Company Stability
• Scam Risk

---

Output

Employer Profile

Overall Score

Pros

Cons

Risk Level

Recommendation

Reasons

Confidence Score

---

Requirements

• Clean Architecture

• SOLID

• Typed Python

• pytest

• mypy

• Documentation

• README update

• Dependency Injection

• Fully deterministic tests

---

The Employer Intelligence Engine must never access websites directly.

It receives normalized employer data from Connectors.

The scoring engine is completely platform-independent.