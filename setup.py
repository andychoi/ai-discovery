"""Setup configuration for AI-Discovery."""

from setuptools import setup, find_packages
from pathlib import Path

# Read requirements
requirements_file = Path(__file__).parent / "app" / "requirements.txt"
with open(requirements_file) as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="ai-discovery",
    version="0.1.0",
    description="Brownfield codebase analysis and SDLC document generation",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Claude",
    author_email="",
    url="https://github.com/yourusername/ai-discovery",
    packages=find_packages(include=["app*"]),
    package_dir={"": "."},
    python_requires=">=3.10",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=8.0",
            "pytest-asyncio>=0.23",
        ],
    },
    entry_points={
        "console_scripts": [
            "discover=app.cli:app",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Documentation",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
