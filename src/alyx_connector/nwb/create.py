from datetime import datetime
from uuid import uuid4
from dateutil import tz
from pathlib import Path

from pynwb import NWBHDF5IO, NWBFile, TimeSeries
from pynwb.behavior import Position, SpatialSeries
from pynwb.file import Subject
from pynwb.misc import AnnotationSeries

from pandas import Series, DataFrame
import numpy as np


def create_file_for_session(session: Series):

    session_start_time = session.start_time  # datetime(2018, 4, 25, 2, 30, 3, tzinfo=tz.gettz("US/Pacific"))

    nwbfile = NWBFile(
        session_description=f"Experimental session of the {session.project} project",  # required
        identifier=session.id,  # required
        session_start_time=session_start_time,  # required
        session_id=session.alias,  # optional
        experimenter=session.users,  # optional
        lab=session.lab,  # optional
        institution=session.institution,  # optional
        experiment_description=session.narrative,  # optional
        keywords=[],  # optional
    )

    file_path = Path(session.path) / f"session_{session.u_alias}_data.nwb"

    with NWBHDF5IO(file_path, "w") as io:  # type: ignore
        io.write(nwbfile)  # type: ignore
