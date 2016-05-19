from setuptools import setup, find_packages

setup(
    name="vxblastsms",
    version="0.1.1",
    url='http://github.com/westerncapelabs/vumi-blastsms',
    license='BSD',
    description="An BlastSMS USSD transport for Vumi.",
    long_description=open('README.rst', 'r').read(),
    author='Western Cape Labs',
    author_email='devops@westerncapelabs.com',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'vumi',
        'Twisted>=13.1.0',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Networking',
    ],
)
