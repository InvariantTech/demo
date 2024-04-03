import itertools
import json
from os import PathLike
from pathlib import Path
import re
import sys

from aerleon import api
from aerleon.lib import naming, yaml
from aerleon.utils.source_map import SourceMap, SourceMapFlatten
from jinja2 import Environment, FileSystemLoader


render_templates_uuid = '765c7f7c-e58b-4061-af81-f72f0b6df7b5'

def render_templates(base_directory: "PathLike",
                     *,
                     policies_directory: "PathLike" = None,
                     definitions_directory: "PathLike" = None,
                     acls_directory: "PathLike" = None,
                     templates_directory: "PathLike" = None,
                     config_source_map_directory: "PathLike" = None,
                     output_directory: "PathLike" = None,):
    if not policies_directory:
        policies_directory = Path(base_directory, 'policies')
    if not definitions_directory:
        definitions_directory = Path(base_directory, 'def')
    if not acls_directory:
        acls_directory = Path(base_directory, 'acl')
    if not templates_directory:
        templates_directory = Path(base_directory, 'templates')
    if not config_source_map_directory:
        config_source_map_directory = Path(base_directory, 'template_source_map')
    if not output_directory:
        output_directory = Path(base_directory, 'configs')
    configs_directory = output_directory

    try:
        definitions = naming.Naming(definitions_directory)
    except naming.Error as err:
        print('Unable to load definitions', err)
        exit(1)

    pols = []
    for pol_path in Path(policies_directory).glob('**/pol/*.yaml'):
        pols.append(yaml.ParseFile(str(pol_path.relative_to(policies_directory)), base_dir=policies_directory, definitions=definitions))
        print(pols[-1].filename)
    api.Generate(pols, definitions, output_directory=acls_directory, source_map=True)

    def pre_process(file: str, file_key: str, file_name: str):
        """Insert boundary markers with data at the beginning of each template variable"""
        boundary_marker = f"FileBoundary(uuid=\"{render_templates_uuid}\", file_key=\"{file_key}\", file_name=\"{file_name}\")"
        return "\n".join([boundary_marker, file, boundary_marker])

    def post_process(file: str):
        """Remove boundary markers and compute source map"""
        boundary_lines = []
        def extract_match(m: re.Match):
            boundary_lines.append(m)
            return ''
        result = re.sub(rf'^FileBoundary\(uuid="{render_templates_uuid}", file_key="([^"]*)", file_name="([^"]*)"\)', extract_match, file, 0, re.MULTILINE)
        a, b = itertools.tee(boundary_lines)
        next(b, None)
        pairs = itertools.islice(zip(a, b), None, None, 2)

        source_map = []
        offset = 0
        line = 0
        line_cursor = 0
        for start, end in pairs:
            # start,end represents the boundary of an inserted template variable,
            # each containing a regex match object for a boundary marker.
            # The boundary markers do not contain newlines.
            # We need to incrementally count the number of newlines between boundaries to determine the true line count
            # (post-offset)

            # span starts at first boundary start (minus offset)
            span_start = start.start() - offset

            # convert to newline count, counting newlines up to span_start
            line = line + result.count('\n', line_cursor, span_start)
            line_cursor = span_start
            span_start = line

            # add removed marker to offset
            offset = offset + (start.end() - start.start())

            # span ends at second boundary start (minus offset)
            span_end = end.start() - offset

            # convert to newline count, counting newlines up to span_start
            line = line + result.count('\n', line_cursor, span_end)
            line_cursor = span_end
            span_end = line

            # add removed marker to offset
            offset = offset + (end.end() - end.start())

            data = {
                "source_file": start[2]
            }
            span_key = f"{span_start}:{span_end}"
            source_map.append({span_key: data})
        source_map = SourceMap(source_map=source_map)
        return result, source_map

    acls = {}
    source_maps = {}
    for path in acls_directory.glob('*'):
        if not path.is_file():
            continue
        if path.name.endswith('.map.json'):
            file_key = path.name.removesuffix('.map.json')
            source_maps[file_key] = SourceMap(source_map=json.loads(path.read_text()))
            continue
        file_key = path.name.replace('.', '_')
        acls[file_key] = pre_process(path.read_text(), file_key=file_key, file_name=path.name)

    env = Environment(loader=FileSystemLoader(templates_directory))

    for template in env.list_templates():
        template_obj = env.get_template(template)
        output_config = template_obj.render(acls)
        output_config, source_map = post_process(output_config)

        # Remove .jinja
        config_path = configs_directory.joinpath(template_obj.name).with_suffix('')
        with open(config_path, 'w') as f:
            f.write(output_config)

        smf = SourceMapFlatten(**source_maps)
        smf.update({config_path.name: source_map})
        source_map = smf.flatten(config_path.name)
        source_map_path = config_source_map_directory.joinpath(template_obj.name).with_suffix('.map.json')
        with open(source_map_path, 'w') as f:
            json.dump(source_map.source_map, f)


def main():
    if not __file__ or len(sys.argv) < 2:
        print("Usage: scripts/render_templates.py snapshot_path", file=sys.stderr)
        exit(1)
    
    # snapshot = '../networks/arista_example'
    snapshot = sys.argv[1]
    network_path = Path(__file__).parent.joinpath(snapshot)

    render_templates(network_path)


if __name__ == '__main__':
    main()
