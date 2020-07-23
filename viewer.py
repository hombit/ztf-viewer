import pathlib
from functools import lru_cache, partial
from urllib.parse import urlencode

import dash_core_components as dcc
import dash_dangerously_set_inner_html as ddsih
import dash_defer_js_import as dji
import dash_html_components as html
import flask
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate
from dash_table import DataTable

from akb import akb
from app import app
from config import is_user_token_valid
from cross import get_catalog_query, find_vizier, find_ztf_oid, find_ztf_circle, vizier_catalog_details, light_curve_features
from data import get_plot_data
from products import DateWithFrac, correct_date
from util import html_from_astropy_table, to_str, INF, min_max_mjd_short, FILTER_COLORS, NotFound, CatalogUnavailable

LIGHT_CURVE_TABLE_COLUMNS = ('mjd', 'mag', 'magerr', 'clrcoeff')

METADATA_FIELDS = ('nobs', 'ngoodobs', 'ngoodobs_short', 'filter', 'coord_string', 'duration', 'duration_short',
                   'fieldid', 'rcid')

MARKER_SIZE = 10

LIST_MAXSHOW = 4


def parse_pathname(pathname):
    path = pathlib.Path(pathname)
    is_short = False
    if path.name == 'short':
        is_short = True
        path = path.parent
    *_, dr, _, oid = path.parts
    return dr, int(oid), is_short


def set_div_for_aladin(oid, version):
    ra, dec = find_ztf_oid.get_coord(oid, version)
    coord = find_ztf_oid.get_coord_string(oid, version)
    style = {'display': 'none'}
    return html.Div(
        [
            html.Div(id='aladin-oid', children=f'{oid}', style=style),
            html.Div(id='aladin-ra', children=f'{ra}', style=style),
            html.Div(id='aladin-dec', children=f'{dec}', style=style),
            html.Div(id='aladin-coord', children=f'{coord}', style=style),
        ],
        id='for-aladin',
    )


@lru_cache(maxsize=128)
def get_layout(pathname):
    dr, oid, is_short = parse_pathname(pathname)
    try:
        find_ztf_oid.find(oid, dr)
    except NotFound:
        return html.H1('404')
    ra, dec = find_ztf_oid.get_coord(oid, dr)
    coord = find_ztf_oid.get_coord_string(oid, dr)
    short_min_mjd, short_max_mjd = min_max_mjd_short(dr)
    min_mjd, max_mjd = (short_min_mjd, short_max_mjd) if is_short else (-INF, INF)
    layout = html.Div([
        html.Div('', id='placeholder', style={'display': 'none'}),
        html.Div(f'{oid}', id='oid', style={'display': 'none'}),
        html.Div(f'{dr}', id='dr', style={'display': 'none'}),
        html.Div(min_mjd, id='min-mjd', style={'display': 'none'}),
        html.Div(max_mjd, id='max-mjd', style={'display': 'none'}),
        dcc.Checklist(
            id='light-curve-time-interval',
            options=[
                {'label': f'"Short" light curve: {short_min_mjd} ≤ MJD ≤ {short_max_mjd}', 'value': 'short'},
            ],
            value=['short'] if is_short else [],
        ),
        html.H2(id='title'),
        html.Div(set_akb_info(0, oid), id='akb-info'),
        html.Div(
            [
                dcc.Graph(
                    id='graph',
                    config={
                        'toImageButtonOptions': {'filename': str(oid)},
                        'displaylogo': False,
                    },
                ),
                html.Div(
                    [
                        'Download ',
                        html.A('PNG', href=f'/{dr}/figure/{oid}?format=png', id='figure-png-link'),
                        ', ',
                        html.A('PDF', href=f'/{dr}/figure/{oid}?format=pdf', id='figure-pdf-link'),
                    ]
                )
            ],
            id='graph-layout',
            style={'width': '70%', 'display': 'inline-block'},
        ),
        html.Div(
            [
                html.Div(className='JS9', id='JS9'),
                dji.Import(src="/static/js/js9_helper.js"),
                html.Div(id='fits-to-show'),
            ],
            style={'width': '20%', 'display': 'inline-block', 'vertical-align': 'top'},
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H2('Neighbours'),
                                html.Div(
                                    [
                                        html.H4('Different passband, same field'),
                                        dcc.Input(
                                            value='1',
                                            id='different_filter_radius',
                                            placeholder='Search radius, arcsec',
                                            type='number',
                                            step='0.1',
                                            min='0',
                                            max='60',
                                        ),
                                        ' search radius, arcsec',
                                        html.Div(id='different_filter_neighbours'),
                                    ],
                                ),
                                html.Div(
                                    [
                                        html.H4('Different field'),
                                        dcc.Input(
                                            value='1',
                                            id='different_field_radius',
                                            placeholder='Search radius, arcsec',
                                            type='number',
                                            step='0.1',
                                            min='0',
                                            max='60',
                                        ),
                                        ' search radius, arcsec',
                                        html.Div(id='different_field_neighbours'),
                                    ],
                                ),
                            ],
                            id='neighbours-layout',
                        ),
                        html.Div(
                            [
                                html.H2('Metadata'),
                                html.Div(id='metadata'),
                            ],
                            id='metadata-layout',
                        ),
                    ],
                    id='neighbours-metadata-layout',
                    style={'width': '70%', 'display': 'inline-block'},
                ),
                html.Div(
                    [
                        html.H2(html.A('Aladin', href=f'//aladin.u-strasbg.fr/AladinLite/?target={coord}')),
                        set_div_for_aladin(oid, dr),
                        html.Div(
                            id='aladin-lite-div',
                            style={'width': '450px', 'height': '450px'},
                        ),
                        dji.Import(src="/static/js/aladin_helper.js"),
                    ],
                    style = {'width': '20%', 'display': 'inline-block', 'vertical-align': 'top'},
                    id='aladin-layout',
                ),
            ],
            id='neighbours-metadata-aladin-layout',
        ),
        html.Div(
            [
                html.H2('GCVS'),
                dcc.Input(
                    value='10',
                    id='gcvs-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                ),
                ' search radius, arcsec',
                html.Div(id='gcvs-table'),
            ],
        ),
        html.Div(
            [
                html.H2('AAVSO VSX'),
                dcc.Input(
                    value='10',
                    id='vsx-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                ),
                ' search radius, arcsec',
                html.Div(id='vsx-table'),
            ],
        ),
        html.Div(
            [
                html.H2('ATLAS catalog of variable stars'),
                dcc.Input(
                    value='10',
                    id='atlas-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                ),
                ' search radius, arcsec',
                html.Div(id='atlas-table'),
            ],
        ),
        html.Div(
            [
                html.H2('ZTF Catalog of Periodic Variable Stars'),
                dcc.Input(
                    value='1',
                    id='ztf-periodic-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                    min='0.1',
                    max='3600',
                    step='0.1',
                ),
                ' search radius, arcsec',
                html.Div(id='ztf-periodic-table'),
            ]
        ),
        html.Div(
            [
                html.H2('Transient Name Server'),
                dcc.Input(
                    value='5',
                    id='tns-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                    step='1',
                ),
                ' search radius, arcsec',
                html.Div(id='tns-table'),
            ]
        ),
        html.Div(
            [
                html.H2('Astrocats'),
                dcc.Input(
                    value='5',
                    id='astrocats-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                    step='1',
                ),
                ' search radius, arcsec',
                html.Div(id='astrocats-table'),
            ]
        ),
        html.Div(
            [
                html.H2('OGLE-III'),
                dcc.Input(
                    value='10',
                    id='ogle-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                    min='0.1',
                    max='323999'
                ),
                ' search radius, arcsec',
                html.Div(id='ogle-table'),
            ],
        ),
        html.Div(
            [
                html.H2('Simbad'),
                dcc.Input(
                    value='50',
                    id='simbad-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                ),
                ' search radius, arcsec',
                html.Div(id='simbad-table'),
            ],
        ),
        html.Div(
            [
                html.H2('Vizier'),
                html.A(
                    'Search on Vizier website within',
                    id='search-on-vizier',
                    href=find_vizier.get_search_url(ra, dec, 0)
                ),
                ' ',
                dcc.Input(
                    value='1',
                    id='vizier-radius',
                    placeholder='Search radius, arcsec',
                    type='number',
                    step='0.1',
                    size='3',
                ),
                ' arcsec',
                html.Button(
                    'Show',
                    id='vizier-button',
                    n_clicks=0,
                    style={'display': 'none'},
                ),
                html.Div(id='vizier-list'),
            ]
        ),
        html.Div(
            [
                html.H2('Features'),
                html.Div(id='features-list'),
            ]
        ),
        html.H2(
            [
                'Download light curve: ',
                html.A('CSV', href=f'/{dr}/csv/{oid}'),
                ', ',
                html.A('JSON', href=find_ztf_oid.json_url(oid, dr)),
            ]
        ),
        html.Div(
            [
                html.H2('Light curve'),
                DataTable(
                    id='light-curve-table',
                    columns=[{'name': column, 'id': column} for column in LIGHT_CURVE_TABLE_COLUMNS],
                ),
            ],
            style={'width': '75%'},
        ),
    ])
    return layout


@app.callback(
    Output('title', 'children'),
    [Input('oid', 'children')],
)
def set_title(oid):
    return f'{oid}'


def set_akb_info(_, oid):
    if not is_user_token_valid(flask.request.cookies.get('login_token')):
        return None
    available_tags = akb.get_tag_names()
    try:
        akb_item = akb.get_by_oid(oid)
        tags_enabled = akb_item['tags']
        description = akb_item['description']
    except NotFound:
        tags_enabled = []
        description = ''
    return [
        dcc.Checklist(
            id='akb-tags',
            options=[{'label': tag, 'value': tag} for tag in available_tags],
            value=tags_enabled,
            labelStyle={'display': 'inline-block'},
        ),
        dcc.Textarea(
            id='akb-description',
            placeholder='Description',
            disabled=False,
            cols=80,
            rows=5,
            value=description,
        ),
        html.Br(),
        html.Button(
            'Submit',
            id='akb-submit',
            n_clicks=0,
        ),
        ' ',
        html.Div('', id='akb-submitted', style={'display': 'inline-block'}),
        html.Br(),
        html.Button(
            'Reset',
            id='akb-reset',
            n_clicks=0,
        ),
    ]


app.callback(
    Output('akb-info', 'children'),
    [Input('akb-reset', 'n_clicks')],
    state=[State('oid', 'children')],
)(set_akb_info)


@app.callback(
    Output('akb-submitted', 'children'),
    [Input('akb-submit', 'n_clicks')],
    state=[
        State('oid', 'children'),
        State('akb-tags', 'value'),
        State('akb-description', 'value'),
    ]
)
def update_akb(n_clicks, oid, tags, description):
    if n_clicks == 0 or n_clicks is None or tags is None:
        raise PreventUpdate
    if description is None:
        description = ''
    try:
        akb.post_object(oid, tags, description)
        return 'Submitted'
    except RuntimeError:
        return 'Error occurred'


@app.callback(
    [
        Output('min-mjd', 'children'),
        Output('max-mjd', 'children'),
    ],
    [Input('light-curve-time-interval', 'value')],
    state=[State('dr', 'children')],
)
def set_min_max_mjd(values, dr):
    if values is None:
        raise PreventUpdate
    if 'short' in values:
        return min_max_mjd_short(dr)
    return -INF, INF


@app.callback(
    Output('metadata', 'children'),
    [
        Input('oid', 'children'),
        Input('dr', 'children')
    ],
)
def get_metadata(oid, dr):
    meta = find_ztf_oid.get_meta(oid, dr).copy()
    meta['coord_string'] = find_ztf_oid.get_coord_string(oid, dr)
    items = [f'**{k}**: {to_str(meta[k])}' for k in METADATA_FIELDS if k in meta]
    column_width = max(map(len, items)) - 2
    div = html.Div(
        html.Ul([html.Li(dcc.Markdown(text)) for text in items], style={'list-style-type': 'none'}),
        style={'columns': f'{column_width}ch'},
    )
    return div


def neighbour_oids(different_filter, different_field):
    if not isinstance(different_filter, list):
        different_filter = []
    if not isinstance(different_field, list):
        different_field = []
    oids = frozenset(div['props']['id'].rsplit('-', maxsplit=1)[-1]
                     for div in different_filter + different_field if isinstance(div, dict))
    return oids


@app.callback(
    Output('graph', 'figure'),
    [
        Input('oid', 'children'),
        Input('dr', 'children'),
        Input('different_filter_neighbours', 'children'),
        Input('different_field_neighbours', 'children'),
        Input('min-mjd', 'children'),
        Input('max-mjd', 'children'),
    ],
)
def set_figure(cur_oid, dr, different_filter, different_field, min_mjd, max_mjd):
    other_oids = neighbour_oids(different_filter, different_field)
    lcs = get_plot_data(cur_oid, dr, other_oids=other_oids, min_mjd=min_mjd, max_mjd=max_mjd)
    mag_min = min(obs['mag'] - obs['magerr'] for obs in lcs)
    mag_max = max(obs['mag'] + obs['magerr'] for obs in lcs)
    mag_ampl = mag_max - mag_min
    range_y = [mag_max + 0.1 * mag_ampl, mag_min - 0.1 * mag_ampl]
    figure = px.scatter(
        pd.DataFrame.from_records(lcs),
        x='mjd_58000',
        y='mag',
        error_y='magerr',
        color='filter',
        range_y=range_y,
        labels={'mjd_58000': 'mjd − 58000'},
        color_discrete_map=FILTER_COLORS,
        symbol='oid',
        size='mark_size',
        size_max=MARKER_SIZE,
        hover_data=['Heliodate'],
        custom_data=['mjd', 'oid', 'fieldid', 'rcid', 'filter'],
    )
    fw = go.FigureWidget(figure)
    fw.layout.hovermode = 'closest'
    fw.layout.xaxis.title.standoff = 0
    fw.layout.yaxis.title.standoff = 0
    fw.layout.legend.orientation = 'h'
    fw.layout.legend.xanchor = 'left'
    fw.layout.legend.y = -0.1
    return fw


def set_figure_link(cur_oid, dr, different_filter, different_field, min_mjd, max_mjd, fmt):
    other_oids = neighbour_oids(different_filter, different_field)
    data = [('other_oid', oid) for oid in other_oids]
    if min_mjd is not None:
        data.append(('min_mjd', min_mjd))
    if max_mjd is not None:
        data.append(('max_mjd', max_mjd))
    data.append(('format', fmt))
    query = urlencode(data)
    return f'/{dr}/figure/{cur_oid}?{query}'


app.callback(
    Output('figure-png-link', 'href'),
    [
        Input('oid', 'children'),
        Input('dr', 'children'),
        Input('different_filter_neighbours', 'children'),
        Input('different_field_neighbours', 'children'),
        Input('min-mjd', 'children'),
        Input('max-mjd', 'children'),
    ],
)(partial(set_figure_link, fmt='png'))


app.callback(
    Output('figure-pdf-link', 'href'),
    [
        Input('oid', 'children'),
        Input('dr', 'children'),
        Input('different_filter_neighbours', 'children'),
        Input('different_field_neighbours', 'children'),
        Input('min-mjd', 'children'),
        Input('max-mjd', 'children'),
    ],
)(partial(set_figure_link, fmt='pdf'))


def find_neighbours(radius, center_oid, dr, different):
    if radius is None:
        return html.P('No radius is specified')
    if float(radius) <= 0:
        return html.P('Radius should be positive')
    ra, dec = find_ztf_oid.get_coord(center_oid, dr)
    kwargs = dict(ra=ra, dec=dec, radius_arcsec=radius, dr=dr)
    fltr = find_ztf_oid.get_meta(center_oid, dr)['filter']
    fieldid = find_ztf_oid.get_meta(center_oid, dr)['fieldid']
    j = find_ztf_circle.find(**kwargs)
    if different == 'filter':
        j = {oid: value for oid, value in j.items()
             if value['meta']['filter'] != fltr and value['meta']['fieldid'] == fieldid}
    elif different == 'fieldid':
        j = {oid: value for oid, value in j.items() if value['meta']['fieldid'] != fieldid}
    else:
        raise ValueError(f'Wrong "different" value {different}')
    children = []
    for i, (oid, obj) in enumerate(sorted(j.items(), key=lambda kv: kv[1]['separation'])):
        div = html.Div(
            [
                html.A(f'{oid}', href=f'./{oid}'),
                f' ({obj["separation"]:.3f}″)'
            ],
            id=f'different-{different}-{oid}',
            style={'display': 'inline'},
        )
        if i != 0:
            div.children.insert(0, ', ')
        children.append(div)
    return children


app.callback(
    Output('different_field_neighbours', 'children'),
    [Input('different_field_radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(find_neighbours, different='fieldid'))

app.callback(
    Output('different_filter_neighbours', 'children'),
    [Input('different_filter_radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(find_neighbours, different='filter'))


app.clientside_callback(
    """
    function(divs) {
        console.log(divs);
        if (divs) {
            let fits = divs[0].props.href;
            console.log(fits);
            let ra = divs[1].props.children;
            console.log(ra);
            let dec = divs[2].props.children;
            console.log(dec);
            JS9.Load(fits, {onload: function(im) {
                JS9.SetPan({ra: ra, dec: dec}, {display: im});
                JS9.AddRegions({shape: 'point', ra: ra, dec: dec}, {display: im});
            }});
        }
        return '';
    }
    """,
    Output('placeholder', 'children'),
    [Input('fits-to-show', 'children')],
)


@app.callback(
    Output('fits-to-show', 'children'),
    [Input('graph', 'clickData')],
    state=[
        State('dr', 'children')
    ]
)
def graph_clicked(data, dr):
    if data is None:
        raise PreventUpdate
    if not (points := data.get('points')):
        raise PreventUpdate
    point = points[0]
    mjd, oid, fieldid, rcid, filter, _ = point['customdata']
    ra, dec = find_ztf_oid.get_coord(oid, dr)
    date = DateWithFrac.from_mjd(mjd, coord=dict(ra=ra, dec=dec))
    correct_date(date)
    url = date.sciimg_path(fieldid=fieldid, rcid=rcid, filter=filter)
    return [
        html.A('Download FITS', href=url, id='fits-to-show-url'),
        html.Div(ra, id='fits-to-show-ra', style={'display': 'none'}),
        html.Div(dec, id='fits-to-show-dec', style={'display': 'none'}),
    ]


def set_table(radius, oid, dr, catalog):
    ra, dec = find_ztf_oid.get_coord(oid, dr)
    if radius is None:
        return html.P('No radius is specified')
    radius = float(radius)
    if radius <= 0:
        return html.P('Radius should be positive')
    query = get_catalog_query(catalog)
    try:
        table = query.find(ra, dec, radius)
    except NotFound:
        return html.P(f'No {catalog} objects within {radius} arcsec from {ra:.5f}, {dec:.5f}')
    except CatalogUnavailable:
        return html.P('Catalog data is temporarily unavailable')
    table = table.copy()
    div = html.Div(
        [
            ddsih.DangerouslySetInnerHTML(html_from_astropy_table(table, query.columns)),
        ],
    )
    return div


app.callback(
    Output('gcvs-table', 'children'),
    [Input('gcvs-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='GCVS'))

app.callback(
    Output('vsx-table', 'children'),
    [Input('vsx-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='VSX'))

app.callback(
    Output('atlas-table', 'children'),
    [Input('atlas-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='ATLAS'))

app.callback(
    Output('ztf-periodic-table', 'children'),
    [Input('ztf-periodic-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='ZTF Periodic'))

app.callback(
    Output('tns-table', 'children'),
    [Input('tns-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='Transient Name Server'))

app.callback(
    Output('astrocats-table', 'children'),
    [Input('astrocats-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='Astrocats'))

app.callback(
    Output('ogle-table', 'children'),
    [Input('ogle-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='OGLE'))

app.callback(
    Output('simbad-table', 'children'),
    [Input('simbad-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ]
)(partial(set_table, catalog='Simbad'))


@app.callback(
    Output('search-on-vizier', 'href'),
    [Input('vizier-radius', 'value')],
    state=[
        State('oid', 'children'),
        State('dr', 'children'),
    ],
)
def set_vizier_url(radius, oid, dr):
    ra, dec = find_ztf_oid.get_coord(oid, dr)
    if radius is None:
        radius = 0
    return find_vizier.get_search_url(ra, dec, radius)


@app.callback(
    Output('vizier-list', 'children'),
    [Input('vizier-button', 'n_clicks')],
    state=[
        State('vizier-radius', 'value'),
        State('oid', 'children'),
        State('dr', 'children'),
    ],
)
def set_vizier_list(n_clicks, radius, oid, dr):
    if n_clicks == 0:
        return ''

    if radius is None:
        return html.P('No radius is specified')

    radius = float(radius)
    ra, dec = find_ztf_oid.get_coord(oid, dr)

    table_list = find_vizier.find(ra, dec, radius)
    if len(table_list) == 0:
        return html.P(f'No vizier catalogs found within {radius} arcsec from {ra:.5f}, {dec:.5f}')

    records = []
    lengths = []
    for catalog, table in zip(table_list.keys(), table_list.values()):
        try:
            description = vizier_catalog_details.description(catalog)
        except NotFound:
            description = catalog
        n = len(table)
        n_objects = str(n) if n < find_vizier.row_limit else f'≥{n}'
        n_objects = f' ({n_objects} objects)' if n > LIST_MAXSHOW else ''
        r = sorted(table['_r'])
        if n > LIST_MAXSHOW:
            r = r[:LIST_MAXSHOW - 1]
        sep = ', '.join(f'{x}″' for x in r)
        if n > LIST_MAXSHOW:
            sep += ', …'
        url = find_vizier.get_catalog_url(catalog, ra, dec, radius)
        records.append(f'[{description}]({url}){n_objects}: {sep}')
        lengths.append(len(description) + len(n_objects) + 2 + len(sep))

    ul_column_width = max(lengths) + 2  # for bullet symbol
    div = html.Div(
        [
            html.Ul([html.Li(dcc.Markdown(record)) for record in records],
                    style={'columns': f'{ul_column_width}ch', 'list-style-type': 'none'}),
        ]
    )
    return div


@app.callback(
    Output('features-list', 'children'),
    [
        Input('oid', 'children'),
        Input('dr', 'children'),
        Input('min-mjd', 'children'),
        Input('max-mjd', 'children'),
    ]
)
def set_features_list(oid, dr, min_mjd, max_mjd):
    try:
        features = light_curve_features(oid, dr, min_mjd=min_mjd, max_mjd=max_mjd)
    except NotFound:
        return 'Not available'
    items = [f'**{k}**: {v:.4g}' for k, v in sorted(features.items(), key=lambda item: item[0])]
    column_width = max(map(len, items)) - 2
    div = html.Div(
        html.Ul([html.Li(dcc.Markdown(text)) for text in items], style={'list-style-type': 'none'}),
        style={'columns': f'{column_width}ch'},
    )
    return div


@app.callback(
    Output('light-curve-table', 'data'),
    [
        Input('oid', 'children'),
        Input('dr', 'children'),
        Input('min-mjd', 'children'),
        Input('max-mjd', 'children'),
    ]
)
def set_lc_table(oid, dr, min_mjd, max_mjd):
    return find_ztf_oid.get_lc(oid, dr, min_mjd=min_mjd, max_mjd=max_mjd)
