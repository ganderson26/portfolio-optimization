# Copyright 2024 D-Wave
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import argparse
from typing import NamedTuple, Union

import dash
import diskcache
from dash import MATCH, DiskcacheManager, ctx
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate
import pandas as pd
import plotly.graph_objs as go
from src.utils import get_live_data
import yfinance as yf

from app_configs import APP_TITLE, THEME_COLOR, THEME_COLOR_SECONDARY
from dash_html import generate_problem_details_table_rows, set_html
from multi_period import MultiPeriod
from single_period import SinglePeriod
from src.enums import SamplerType

cache = diskcache.Cache("./cache")
background_callback_manager = DiskcacheManager(cache)

# Fix Dash long callbacks crashing on macOS 10.13+ (also potentially not working
# on other POSIX systems), caused by https://bugs.python.org/issue33725
# (aka "beware of multithreaded process forking").
#
# Note: default start method has already been changed to "spawn" on darwin in
# the `multiprocessing` library, but its fork, `multiprocess` still hasn't caught up.
# (see docs: https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
import multiprocess

if multiprocess.get_start_method(allow_none=True) is None:
    multiprocess.set_start_method("spawn")

app = dash.Dash(
    __name__,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    prevent_initial_callbacks="initial_duplicate",
    background_callback_manager=background_callback_manager,
)
app.title = APP_TITLE

server = app.server
app.config.suppress_callback_exceptions = True

# Parse debug argument
parser = argparse.ArgumentParser(description="Dash debug setting.")
parser.add_argument(
    "--debug",
    action="store_true",
    help="Add argument to see Dash debug menu and get live reload updates while developing.",
)

args = parser.parse_args()
DEBUG = args.debug

print(f"\nDebug has been set to: {DEBUG}")
if not DEBUG:
    print(
        "The app will not show live code updates and the Dash debug menu will be hidden.",
        "If editting code while the app is running, run the app with `python app.py --debug`.\n",
        sep="\n"
    )

# Generates css file and variable using THEME_COLOR and THEME_COLOR_SECONDARY settings
css = f"""/* Automatically generated theme settings css file, see app.py */
:root {{
    --theme: {THEME_COLOR};
    --theme-secondary: {THEME_COLOR_SECONDARY};
}}
"""
with open("assets/custom_00_theme.css", "w") as f:
    f.write(css)


@app.callback(
    Output({"type": "to-collapse-class", "index": MATCH}, "className"),
    inputs=[
        Input({"type": "collapse-trigger", "index": MATCH}, "n_clicks"),
        State({"type": "to-collapse-class", "index": MATCH}, "className"),
    ],
    prevent_initial_call=True,
)
def toggle_left_column(collapse_trigger: int, to_collapse_class: str) -> str:
    """Toggles a 'collapsed' class that hides and shows some aspect of the UI.

    Args:
        collapse_trigger (int): The (total) number of times a collapse button has been clicked.
        to_collapse_class (str): Current class name of the thing to collapse, 'collapsed' if not
            visible, empty string if visible.

    Returns:
        str: The new class name of the thing to collapse.
    """

    classes = to_collapse_class.split(" ") if to_collapse_class else []
    if "collapsed" in classes:
        classes.remove("collapsed")
        return " ".join(classes)
    return to_collapse_class + " collapsed" if to_collapse_class else "collapsed"


def generate_graph(
    df: pd.DataFrame = None
) -> go.Figure:
    """Generates graph given df.

    Args:
        df (pd.DataFrame): A DataFrame containing the data to plot.

    Returns:
        go.Figure: A Plotly figure object.
    """
    fig = go.Figure()

    for col in list(df.columns.values):
        if col != "Month":
            fig.add_trace(go.Scatter(x=df["Month"] if "Month" in df.columns else df.index, y=df[col], mode="lines", name=col))

    fig.update_layout(
        title="Historical Stock Data", xaxis_title="Month", yaxis_title="Price"
    )

    return fig


@app.callback(
    Output("input-graph", "figure"),
    inputs=[
        Input("period-options", "value"),
    ],
)
def render_initial_state(period_value: int) -> str:
    """Runs on load and any time the value of the slider is updated.
        Add `prevent_initial_call=True` to skip on load runs.

    Args:
        slider_value: The value of the slider.

    Returns:
        str: The content of the input tab.
    """
    if period_value:
        num = 0
        dates = ['2010-01-01', '2012-12-31']
        stocks = ['AAPL', 'MSFT', 'AAL', 'WMT']
        baseline = ['^GSPC']
        df, stocks, df_baseline = get_live_data(num, dates, stocks, baseline)
    else:
        df = pd.read_csv('data/basic_data.csv')

    return generate_graph(df)


class RunOptimizationReturn(NamedTuple):
    """Return type for the ``run_optimization`` callback function."""

    results: str = dash.no_update
    problem_details_table: list = dash.no_update
    # Add more return variables here. Return values for callback functions
    # with many variables should be returned as a NamedTuple for clarity.


@app.callback(
    # The Outputs below must align with `RunOptimizationReturn`.
    # Output("results", "children"),
    Output("problem-details", "children"),
    background=True,
    inputs=[
        Input("run-button", "n_clicks"),
        State("sampler-type-select", "value"),
        State("solver-time-limit", "value"),
        State("budget", "value"),
        State("transaction-cost", "value"),
        # State("num-stocks", "value"),
        State("period-options", "value"),
        # State("date-range", "start_date"),
        # State("date-range", "end_date"),
    ],
    running=[
        # Shows cancel button while running.
        (Output("cancel-button", "className"), "", "display-none"),
        (Output("run-button", "className"), "display-none", ""),  # Hides run button while running.
        (Output("results-tab", "disabled"), True, False),  # Disables results tab while running.
        (Output("results-tab", "label"), "Loading...", "Results"),
        (Output("tabs", "value"), "input-tab", "input-tab"),  # Switch to input tab while running.
        (Output("run-in-progress", "data"), True, False),  # Can block certain callbacks.
    ],
    cancel=[Input("cancel-button", "n_clicks")],
    prevent_initial_call=True,
)
def run_optimization(
    # The parameters below must match the `Input` and `State` variables found
    # in the `inputs` list above.
    run_click: int,
    sampler_type: Union[SamplerType, int],
    time_limit: float,
    budget: int,
    transaction_cost: list,
    # num_stocks: int,
    period_options: int,
    # start_date: date,
    # end_date: date,
) -> RunOptimizationReturn:
    """Runs the optimization and updates UI accordingly.

    This is the main function which is called when the `Run Optimization` button is clicked.
    This function takes in all form values and runs the optimization, updates the run/cancel
    buttons, deactivates (and reactivates) the results tab, and updates all relevant HTML
    components.

    Args:
        run_click: The (total) number of times the run button has been clicked.
        sampler_type: Either Quantum Hybrid (``0`` or ``SamplerType.HYBRID``),
            or Classical (``1`` or ``SamplerType.CLASSICAL``).
        time_limit: The solver time limit.
        slider_value: The value of the slider.
        dropdown_value: The value of the dropdown.
        checklist_value: A list of the values of the checklist.
        radio_value: The value of the radio.

    Returns:
        A NamedTuple (RunOptimizationReturn) containing all outputs to be used when updating the HTML
        template (in ``dash_html.py``). These are:

            results: The results to display in the results tab.
            problem-details: List of the table rows for the problem details table.
    """

    # Only run optimization code if this function was triggered by a click on `run-button`.
    # Setting `Input` as exclusively `run-button` and setting `prevent_initial_call=True`
    # also accomplishes this.
    if run_click == 0 or ctx.triggered_id != "run-button":
        raise PreventUpdate

    if isinstance(sampler_type, int):
        sampler_type = SamplerType(sampler_type)

    if period_options:
        print(f"\nRebalancing portfolio optimization run...")

        my_portfolio = MultiPeriod(
            budget=budget,
            sampler_args="{}",
            gamma=True,
            model_type=sampler_type,
            stocks=['AAPL', 'MSFT', 'AAL', 'WMT'],
            file_path='data/basic_data.csv',
            alpha=[0.005],
            baseline="^GSPC",
            t_cost=0
        )

    else:
        print(f"\nSingle period portfolio optimization run...")

        my_portfolio = SinglePeriod(
            budget=budget,
            sampler_args="{}",
            model_type=sampler_type,
            stocks=['AAPL', 'MSFT', 'AAL', 'WMT'],
            file_path='data/basic_data.csv',
            alpha=[0.005],
            t_cost=transaction_cost,
        )

    solution = my_portfolio.run(min_return=0, max_risk=0, num=0)

    # Generates a list of table rows for the problem details table.
    problem_details_table = generate_problem_details_table_rows(
        solver="CQM" if sampler_type is SamplerType.CQM else "DQM",
        time_limit=time_limit,
    )

    # return RunOptimizationReturn(
    #     # results="Put demo results here.",
    #     problem_details_table=problem_details_table,
    # )
    return problem_details_table


# Imports the Dash HTML code and sets it in the app.
# Creates the visual layout and app (see `dash_html.py`)
set_html(app)

# Run the server
if __name__ == "__main__":
    app.run_server(debug=DEBUG)