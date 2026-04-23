from setuptools import setup, find_packages

setup(
    name="TethrLink",
    version="1.0.0",
    description="Wired Second Monitor via USB Tethering",
    long_description="TethrLink turns an Android tablet into a wired second monitor for your Linux PC using USB tethering.",
    long_description_content_type="text/plain",
    author="Prince Savsaviya",
    author_email="princesavsaviya2023.learning@gmail.com",
    url="https://github.com/princesavsaviya/TethrLink",
    license="GPL-3.0-or-later",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "PyGObject",
        "pystray",
        "mss",
    ],
    entry_points={
        "console_scripts": [
            "tethrlink=server.app:main",
        ],
    },
    package_data={
        "server": ["icons/*.png", "ui/*.css"],
    },
    data_files=[
        ("share/applications", ["desktop/tethrlink.desktop"]),
        ("share/icons/hicolor/512x512/apps", ["server/icons/tethrlink.png"]),
    ],
)
