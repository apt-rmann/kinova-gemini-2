from setuptools import find_packages, setup

package_name = 'gemini_agent'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='mcrr-lab',
    maintainer_email='jxm1536@case.edu',
    description='Gemini Live agent that drives the Kinova arm through the kortex_controller actions',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'agent_node = gemini_agent.agent_node:main',
            'tool_cli = gemini_agent.tool_cli:main',
        ],
    },
)
