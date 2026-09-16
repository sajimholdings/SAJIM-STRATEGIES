# Contributing to Backtrader Pullback Window Strategy

Thank you for your interest in contributing to this project! 🎉

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Testing Guidelines](#testing-guidelines)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Enhancements](#suggesting-enhancements)

---

## 🤝 Code of Conduct

This project and everyone participating in it is governed by our commitment to providing a welcoming and inclusive environment. Please be respectful and constructive in all interactions.

**Expected Behavior:**
- Use welcoming and inclusive language
- Be respectful of differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

---

## 🎯 How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues to avoid duplicates.

**When reporting a bug, include:**
- **Clear title** - Brief description of the problem
- **Steps to reproduce** - Detailed steps to reproduce the behavior
- **Expected behavior** - What you expected to happen
- **Actual behavior** - What actually happened
- **Environment** - Python version, Backtrader version, OS
- **Code samples** - Minimal reproducible example
- **Error messages** - Full error traceback if applicable

### Suggesting Enhancements

Enhancement suggestions are welcome! Please provide:
- **Clear use case** - Why this enhancement would be useful
- **Detailed description** - How it should work
- **Examples** - Code samples or mockups if applicable
- **Alternatives** - Other solutions you've considered

### Code Contributions

Contributions to improve strategy logic, add features, or fix bugs are welcome!

---

## 🛠️ Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/backtrader-pullback-window-usdchf.git
cd backtrader-pullback-window-usdchf
```

### 2. Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies

```bash
# Install requirements
pip install -r requirements.txt

# Install development dependencies (if any)
pip install pytest pytest-cov black flake8
```

### 4. Create Feature Branch

```bash
# Create a new branch for your feature
git checkout -b feature/your-feature-name

# Or for bug fixes:
git checkout -b fix/your-bug-fix
```

---

## 📝 Coding Standards

### Python Style Guide

This project follows **PEP 8** style guidelines with some exceptions:

- **Line length**: 100 characters (not 79)
- **Indentation**: 4 spaces (no tabs)
- **Naming conventions**:
  - Classes: `PascalCase`
  - Functions/methods: `snake_case`
  - Constants: `UPPER_CASE`
  - Private methods: `_leading_underscore`

### Code Formatting

```bash
# Format your code with black (recommended)
black src/strategy/

# Check code style with flake8
flake8 src/strategy/ --max-line-length=100
```

### Documentation

- Add docstrings to all functions and classes
- Use clear, descriptive variable names
- Comment complex logic
- Update README if adding new features

**Docstring Example:**
```python
def calculate_position_size(account_value, risk_percent, stop_distance):
    """
    Calculate position size based on risk percentage.
    
    Args:
        account_value (float): Current account equity
        risk_percent (float): Percentage of account to risk (e.g., 0.01 for 1%)
        stop_distance (float): Distance to stop loss in price units
        
    Returns:
        float: Position size in units
        
    Example:
        >>> calculate_position_size(100000, 0.01, 0.0050)
        2000.0
    """
    risk_amount = account_value * risk_percent
    position_size = risk_amount / stop_distance
    return position_size
```

---

## 🧪 Testing Guidelines

### Running Tests

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_ogle_entry_system.py
```

### Writing Tests

- Place test files in the `tests/` directory
- Name test files: `test_*.py`
- Name test functions: `test_*`
- Use descriptive test names
- Test both success and failure cases

**Test Example:**
```python
def test_ema_crossover_signal():
    """Test that EMA crossover generates correct signal."""
    # Setup
    fast_ema = [1.2000, 1.2010, 1.2020]
    slow_ema = [1.2005, 1.2008, 1.2012]
    
    # Execute
    signal = check_crossover(fast_ema, slow_ema)
    
    # Assert
    assert signal == "BUY", "Expected BUY signal on bullish crossover"
```

### Strategy Testing

When modifying strategy logic:
1. Run backtests on multiple timeframes
2. Verify performance metrics remain reasonable
3. Check for unintended side effects
4. Document changes in performance

---

## 📤 Pull Request Process

### Before Submitting

- [ ] Code follows style guidelines
- [ ] All tests pass locally
- [ ] New tests added for new features
- [ ] Documentation updated
- [ ] Commit messages are clear and descriptive
- [ ] No debugging code or print statements left
- [ ] No merge conflicts with main branch

### Commit Message Format

Use clear, descriptive commit messages:

```
feat: Add volatility expansion filter to entry logic

- Implemented ATR-based volatility check
- Added configurable threshold parameter
- Updated tests to cover new functionality
```

**Prefixes:**
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `test:` - Test additions or changes
- `refactor:` - Code refactoring
- `perf:` - Performance improvements
- `style:` - Code style changes (formatting)

### PR Description Template

```markdown
## Description
Brief description of what this PR does

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Changes Made
- List key changes
- Explain important decisions

## Testing
- Describe testing performed
- Include test results if applicable

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Tests added/updated
- [ ] Documentation updated
```

### Review Process

1. **Submit PR** - Create pull request with clear description
2. **CI Checks** - Automated tests must pass
3. **Code Review** - Maintainer will review your code
4. **Address Feedback** - Make requested changes
5. **Approval** - PR approved by maintainer
6. **Merge** - Changes merged into main branch

---

## 🐛 Reporting Bugs

### Bug Report Template

```markdown
**Describe the bug**
A clear description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Configure strategy with parameters '...'
2. Run backtest on '...'
3. See error

**Expected behavior**
What you expected to happen.

**Actual behavior**
What actually happened.

**Screenshots/Logs**
If applicable, add screenshots or error logs.

**Environment:**
 - OS: [e.g., Windows 10]
 - Python Version: [e.g., 3.10.0]
 - Backtrader Version: [e.g., 1.9.76.123]
 - Other dependencies: [list if relevant]

**Additional context**
Any other context about the problem.
```

---

## 💡 Suggesting Enhancements

### Enhancement Proposal Template

```markdown
**Is your feature request related to a problem?**
A clear description of what the problem is.

**Describe the solution you'd like**
A clear description of what you want to happen.

**Describe alternatives you've considered**
Other solutions or features you've considered.

**Use case**
How would this enhancement be used?

**Example**
Code examples or mockups if applicable.

**Additional context**
Any other context or screenshots.
```

---

## 📚 Resources

### Backtrader Documentation
- [Official Backtrader Docs](https://www.backtrader.com/docu/)
- [Backtrader GitHub](https://github.com/mementum/backtrader)

### Trading Strategy Resources
- [Investopedia - Technical Analysis](https://www.investopedia.com/technical-analysis-4689657)
- [Babypips - Forex Education](https://www.babypips.com/learn/forex)

### Python Resources
- [PEP 8 Style Guide](https://pep8.org/)
- [Python Documentation](https://docs.python.org/3/)

---

## 🙏 Thank You!

Your contributions help make this project better for everyone. We appreciate your time and effort!

If you have questions, feel free to:
- Open a discussion issue
- Ask questions in pull requests
- Reach out to maintainers

**Happy Trading! 📈**
