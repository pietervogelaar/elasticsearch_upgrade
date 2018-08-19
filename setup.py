from setuptools import setup


def readme():
    with open('README.md') as f:
        return f.read()


setup(
    name='elasticsearch_upgrade',
    version='0.2.1',
    description='Performs a rolling upgrade of an Elasticsearch cluster.',
    long_description=readme(),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    keywords='elasticsearch rolling upgrade',
    url='https://github.com/pietervogelaar/elasticsearch_upgrade',
    author='Pieter Vogelaar',
    author_email='pieter@pietervogelaar.nl',
    license='MIT',
    install_requires=[
        'requests',
    ],
    scripts=['elasticsearch_upgrade.py'],
    include_package_data=True,
    zip_safe=False)
