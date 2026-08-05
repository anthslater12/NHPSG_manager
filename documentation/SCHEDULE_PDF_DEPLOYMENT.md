# Schedule PDF deployment note

The Schedule PDF export uses `WeasyPrint==62.3`. This pin supports the
application's Python 3.10 runtime and uses the public WeasyPrint Python API.

Before production installation, verify the actual Ubuntu release and Python
runtime. The following native packages are the expected starting point for
this WeasyPrint generation on Ubuntu:

```text
python3-dev python3-pip python3-venv libpango-1.0-0 libpangoft2-1.0-0
libharfbuzz0b libharfbuzz-subset0 libffi-dev libjpeg-dev libopenjp2-7-dev
```

The exact package names and availability must be checked against the actual
production Ubuntu release before installation. No package installation is
performed by this task.

Install the pinned Python dependency inside the application's virtual
environment:

```text
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -c "from weasyprint import HTML; print('WeasyPrint OK')"
```

After installing dependencies, restart the NHPSG application using the
existing production deployment procedure. This task does not invent or
change a service name and does not alter system services.

For rollback, restore the previous application release and its dependency
lock/requirements state, then restart the application using the existing
deployment procedure. Do not remove shared system packages unless their
ownership and impact have been verified.
