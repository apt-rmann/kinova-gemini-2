from setuptools import find_packages, setup

package_name = 'app_interface'

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
    description='Transports that let the phone app publish instructions to /user_instructions',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'ble_interface = app_interface.ble_interface_node:main',
            'bluetooth_interface = app_interface.bluetooth_interface_node:main',
            'network_interface = app_interface.network_interface_node:main',
        ],
    },
)
