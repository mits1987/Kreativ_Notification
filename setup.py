from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="kreativ_notification",
    version="0.0.1",
    description="Unified WhatsApp/notification infrastructure for Kreativ Gravures",
    author="Mitesh",
    author_email="info@kreativ.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)