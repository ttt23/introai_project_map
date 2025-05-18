import streamlit as st
import osmnx as ox
import folium
from streamlit_folium import st_folium
import networkx as nx
import os
from shapely.geometry import Point, LineString, MultiLineString
import math
import json
import requests
from folium.plugins import MarkerCluster

st.set_page_config(page_title="Bản đồ chỉ đường Giảng Võ - Ba Đình", layout="wide")
st.title("Bản đồ chỉ đường Giảng Võ - Ba Đình")

CENTER = [21.0285, 105.8342]

@st.cache_data
def load_roads():
    with open('roads.json', 'r', encoding='utf-8') as f:
        return json.load(f)

roads = load_roads()

def get_ways_by_name_osm(name, city="Hà Nội"):
    """Truy vấn Overpass API để lấy các đoạn đường (ways) theo tên ở một thành phố."""
    overpass_url = "http://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    area["name"="{city}"]->.searchArea;
    (
      way["name"="{name}"](area.searchArea);
      way["name"~"^{name}$",i](area.searchArea);
      way["name"~"{name}",i](area.searchArea);
    );
    out geom;
    """
    try:
        response = requests.get(overpass_url, params={'data': query})
        response.raise_for_status() 
        data = response.json()
        if 'elements' in data and data['elements']:
            return data['elements']
        else:
            return []
    except requests.exceptions.HTTPError as http_err:
        # Có thể log lỗi này ra terminal nếu muốn, thay vì st.error
        print(f"Lỗi HTTP khi gọi Overpass API cho '{name}': {http_err}")
        return []
    except requests.exceptions.RequestException as req_err:
        print(f"Lỗi Request khi gọi Overpass API cho '{name}': {req_err}")
        return []
    except json.JSONDecodeError as json_err:
        print(f"Lỗi giải mã JSON từ Overpass API cho '{name}': {json_err}")
        return []

def ban_all_roads_get_ids(roads_data_json, city="Hà Nội"):
    banned_ids = set()
    all_road_names_to_query = set()
    for district in roads_data_json:
        for road_name in roads_data_json[district]:
            if "xe đạp sông tô lịch" not in road_name.lower():
                 all_road_names_to_query.add(road_name)
    
    for road_name in all_road_names_to_query:
        if road_name in st.session_state.road_name_to_osm_ids_cache:
            osm_ids_for_road = st.session_state.road_name_to_osm_ids_cache[road_name]
        else:
            try:
                ways = get_ways_by_name_osm(road_name, city)
                osm_ids_for_road = {way['id'] for way in ways}
                st.session_state.road_name_to_osm_ids_cache[road_name] = osm_ids_for_road
            except Exception as e:
                print(f"Lỗi khi lấy OSM ID cho '{road_name}' trong ban_all_roads_get_ids: {e}")
                osm_ids_for_road = set()
        banned_ids.update(osm_ids_for_road)
    return banned_ids 

if 'restricted_segments' not in st.session_state:
    st.session_state.restricted_segments = []
if 'restricted_road_names' not in st.session_state:
    st.session_state.restricted_road_names = []
if 'road_name_to_osm_ids_cache' not in st.session_state:
    st.session_state.road_name_to_osm_ids_cache = {}
if 'banned_osm_ids_from_multiselect' not in st.session_state:
    st.session_state.banned_osm_ids_from_multiselect = set()
if 'banned_osm_ids_from_full_ban' not in st.session_state:
    st.session_state.banned_osm_ids_from_full_ban = set()
if 'is_full_ban_active' not in st.session_state:
    st.session_state.is_full_ban_active = False

if 'multi_dd' not in st.session_state: st.session_state.multi_dd = []
if 'multi_bd' not in st.session_state: st.session_state.multi_bd = []

if 'points' not in st.session_state: st.session_state.points = []

# Session state cho tính năng "Xác nhận Cấm bằng Click"
if 'ban_by_click_mode' not in st.session_state:
    st.session_state.ban_by_click_mode = False
if 'clicked_banned_osm_ids' not in st.session_state: # Đảm bảo được khởi tạo
    st.session_state.clicked_banned_osm_ids = set()
if 'pending_ban_edge_info' not in st.session_state: # Thông tin của cạnh đang chờ xác nhận cấm
    st.session_state.pending_ban_edge_info = None 
    # Sẽ lưu dạng: {'osmid': ..., 'geometry': ..., 'name': ..., 'length': ..., 'u': ..., 'v': ..., 'key': ...}

if 'suggested_roads' not in st.session_state:
    st.session_state.suggested_roads = []

if 'suggested_roads_selected' not in st.session_state:
    st.session_state.suggested_roads_selected = set()

# 1. Khởi tạo trạng thái
if 'show_nodes' not in st.session_state:
    st.session_state.show_nodes = False

# 1. Thêm biến trạng thái
if 'show_edges' not in st.session_state:
    st.session_state.show_edges = False

# --- KẾT THÚC KHỞI TẠO SESSION STATE ---

@st.cache_data
def load_map_data():
    if os.path.exists("giang_vo_ba_dinh.graphml"):
        st.write("Đang tải dữ liệu bản đồ từ file...")
        G = ox.load_graphml("giang_vo_ba_dinh.graphml")
    else:
        st.write("Đang tải dữ liệu bản đồ từ OSM...")
        G = ox.graph_from_place(
            ["Giảng Võ, Ba Đình, Hà Nội"],
            network_type="all"
        )
        ox.save_graphml(G, "giang_vo_ba_dinh.graphml")
    st.write("Đã tải xong dữ liệu bản đồ!")
    return G

def add_restricted_segment(G, start_point, end_point, description):
    start_node = ox.nearest_nodes(G, start_point[1], start_point[0])
    end_node = ox.nearest_nodes(G, end_point[1], end_point[0])
    line = LineString([(G.nodes[start_node]['x'], G.nodes[start_node]['y']),
                      (G.nodes[end_node]['x'], G.nodes[end_node]['y'])])
    st.session_state.restricted_segments.append({
        'start': start_point,
        'end': end_point,
        'line': line,
        'description': description
    })

def is_segment_restricted(G, u, v, restricted_segments_arg_unused):
    banned_by_click = st.session_state.get('clicked_banned_osm_ids', set())

    if not banned_by_click:
        return False

    edge_data = G.get_edge_data(u, v)
    if edge_data:
        actual_edge_data = edge_data[0] if isinstance(edge_data, dict) and 0 in edge_data else edge_data
        osmid_data = actual_edge_data.get('osmid')
        if osmid_data:
            if isinstance(osmid_data, list):
                for oid in osmid_data:
                    if oid in banned_by_click:
                        return True
            elif osmid_data in banned_by_click:
                return True
    return False

def get_route_instructions(G, route):
    instructions = []
    total_distance = 0
    current_street = None
    current_distance = 0
    turn_direction = ""
    for i in range(len(route)-1):
        u, v = route[i], route[i+1]
        edge_data = G.get_edge_data(u, v)
        if edge_data:
            edge = edge_data[0] if isinstance(edge_data, dict) and 0 in edge_data else edge_data
            distance = edge.get('length', 0)
            total_distance += distance
            street_name = edge.get('name', 'Đường không tên')
            if isinstance(street_name, list):
                street_name = street_name[0]
            if i > 0:
                prev_u = route[i-1]
                p1 = (G.nodes[prev_u]['y'], G.nodes[prev_u]['x'])
                p2 = (G.nodes[u]['y'], G.nodes[u]['x'])
                p3 = (G.nodes[v]['y'], G.nodes[v]['x'])
                v1 = (p2[0] - p1[0], p2[1] - p1[1])
                v2 = (p3[0] - p2[0], p3[1] - p2[1])
                dot_product = v1[0]*v2[0] + v1[1]*v2[1]
                v1_mag = (v1[0]**2 + v1[1]**2)**0.5
                v2_mag = (v2[0]**2 + v2[1]**2)**0.5
                if v1_mag == 0 or v2_mag == 0:
                    cos_angle = 1.0
                else:
                    cos_angle = dot_product / (v1_mag * v2_mag)
                cos_angle = max(min(cos_angle, 1.0), -1.0)
                angle = math.acos(cos_angle)
                angle_degrees = math.degrees(angle)
                cross_product = v1[0]*v2[1] - v1[1]*v2[0]
                turn_direction = ""
                if angle_degrees > 30:
                    if cross_product > 0:
                        turn_direction = "rẽ phải"
                    else:
                        turn_direction = "rẽ trái"
            if current_street is None or street_name != current_street:
                if current_street is not None:
                    instructions.append(f"Đi {current_distance:.0f}m trên {current_street}")
                current_street = street_name
                current_distance = distance
                if i > 0 and turn_direction:
                    instructions[-1] += f", sau đó {turn_direction}"
            else:
                current_distance += distance
    if current_street is not None:
        instructions.append(f"Đi {current_distance:.0f}m trên {current_street}")
    return instructions, total_distance

def find_shortest_path(G, start_point, end_point):
    start_node = ox.nearest_nodes(G, start_point[1], start_point[0])
    end_node = ox.nearest_nodes(G, end_point[1], end_point[0])
    def weight_function(u, v, d):
        if is_segment_restricted(G, u, v, None):
            return None 
        return d.get('length', 1)
    try:
        route = nx.astar_path(G, start_node, end_node, weight=weight_function)
        return route
    except nx.NetworkXNoPath:
        return None

def find_nearest_roads(G, point, num_roads=20, max_distance=0.002):
    """Tìm num_roads tuyến đường gần nhất với điểm cho trước (max_distance ~200m)."""
    lon, lat = point
    nearest_edges = []
    pt = Point(lon, lat)
    for u, v, k, data in G.edges(data=True, keys=True):
        if 'geometry' in data:
            geom = data['geometry']
            if isinstance(geom, MultiLineString):
                # Tính khoảng cách nhỏ nhất từ điểm tới từng LineString con
                distance = min(line.distance(pt) for line in geom.geoms)
            else:
                distance = geom.distance(pt)
            if distance <= max_distance:
                nearest_edges.append({
                    'u': u,
                    'v': v,
                    'key': k,
                    'data': data,
                    'distance': distance
                })
    nearest_edges.sort(key=lambda x: x['distance'])
    return nearest_edges[:num_roads]

def create_map(G, points=None, route=None, suggested_roads=None, show_nodes=False, show_edges=False):
    m = folium.Map(location=CENTER, zoom_start=14)
    # Vẽ các tuyến đường gợi ý (nếu có)
    if suggested_roads:
        # Lọc các tuyến chưa bị cấm
        filtered = []
        for idx, road in enumerate(suggested_roads):
            data = road['data']
            osmid = data.get('osmid')
            is_banned = False
            if isinstance(osmid, list):
                if any(oid in st.session_state.clicked_banned_osm_ids for oid in osmid):
                    is_banned = True
            elif osmid in st.session_state.clicked_banned_osm_ids:
                is_banned = True
            if not is_banned:
                filtered.append((idx, road))
        suggested_colors = ['blue', 'green', 'orange', 'red', 'brown']
        for order, (idx, road) in enumerate(filtered):
            data = road['data']
            coords = [(coord[1], coord[0]) for coord in data['geometry'].coords]
            color = suggested_colors[order % len(suggested_colors)]
            folium.PolyLine(
                coords,
                weight=6,
                color=color,
                opacity=0.8,
                popup=f"{order+1}. {data.get('name', 'Đường không tên')}"
            ).add_to(m)
    # 1. Vẽ các cạnh đã bị cấm chính thức (màu tím)
    if st.session_state.clicked_banned_osm_ids:
        for u_b, v_b, data_b in G.edges(data=True):
            osmid_b = data_b.get('osmid')
            is_officially_banned = False
            if osmid_b:
                if isinstance(osmid_b, list):
                    if any(oid in st.session_state.clicked_banned_osm_ids for oid in osmid_b):
                        is_officially_banned = True
                elif osmid_b in st.session_state.clicked_banned_osm_ids:
                    is_officially_banned = True
            if is_officially_banned and 'geometry' in data_b:
                coords_b = [(coord[1], coord[0]) for coord in data_b['geometry'].coords]
                folium.PolyLine(coords_b, weight=6, color='purple', opacity=0.8, 
                                popup=f"Đã cấm (OSM ID(s): {osmid_b})").add_to(m)
    # 2. Vẽ đoạn đang chờ xác nhận cấm (màu vàng)
    if st.session_state.pending_ban_edge_info:
        pending_info = st.session_state.pending_ban_edge_info
        if pending_info.get('geometry'):
            is_already_banned = False
            if isinstance(pending_info['osmid'], list):
                if any(oid in st.session_state.clicked_banned_osm_ids for oid in pending_info['osmid']):
                    is_already_banned = True
            elif pending_info['osmid'] in st.session_state.clicked_banned_osm_ids:
                is_already_banned = True
            if not is_already_banned:
                coords_pending = [(coord[1], coord[0]) for coord in pending_info['geometry'].coords]
                folium.PolyLine(coords_pending, weight=7, color='yellow', opacity=0.9,
                                popup=f"Đang chọn để cấm: {pending_info.get('name', '')} (OSM: {pending_info.get('osmid', '')})").add_to(m)
    if points:
        if len(points) > 0:
            folium.Marker(points[0], popup='Điểm bắt đầu', icon=folium.Icon(color='green')).add_to(m)
        if len(points) > 1:
            folium.Marker(points[1], popup='Điểm kết thúc', icon=folium.Icon(color='red')).add_to(m)
    if route:
        route_coords = [[G.nodes[n]['y'], G.nodes[n]['x']] for n in route]
        folium.PolyLine(route_coords, weight=5, color='blue', opacity=0.9).add_to(m)
    # Thêm marker cho tất cả node nếu show_nodes=True
    if show_nodes:
        for node_id, data in G.nodes(data=True):
            lat = data['y']
            lon = data['x']
            folium.Marker(
                [lat, lon],
                icon=folium.Icon(color='blue', icon='info-sign'),
                popup=f"Node {node_id}"
            ).add_to(m)
    # Thêm tất cả các tuyến đường nếu show_edges
    if show_edges:
        for u, v, data in G.edges(data=True):
            if 'geometry' in data:
                geom = data['geometry']
                if hasattr(geom, 'coords'):
                    coords = [(y, x) for x, y in geom.coords]
                    folium.PolyLine(coords, color='blue', weight=3, opacity=0.7).add_to(m)
    return m

with st.sidebar:
    st.header("Hiển thị node/path")
    #  Thêm check box sidebar
    st.session_state.show_nodes = st.sidebar.checkbox(
    "Hiển thị node", 
    value=st.session_state.show_nodes
    )
    st.session_state.show_edges = st.sidebar.checkbox(
        "Hiển thị path", 
        value=st.session_state.show_edges
    )
    st.header("Quản lý đoạn đường bị cấm")

    # st.subheader("Cấm đường bằng Click")
    # Nút kích hoạt chế độ sẽ được đọc giá trị từ session_state
    # và khi thay đổi sẽ tự động cập nhật session_state nhờ key
    st.session_state.ban_by_click_mode = st.checkbox(
        "Kích hoạt chế độ cấm đường bằng Click", 
        value=st.session_state.get('ban_by_click_mode', False), 
        key="cb_ban_by_click_mode_state"
    )
    # Nếu tắt chế độ cấm, xóa thông tin pending ban
    if not st.session_state.ban_by_click_mode and st.session_state.pending_ban_edge_info:
        st.session_state.pending_ban_edge_info = None
        # st.rerun() # Không cần rerun ngay, lần vẽ map sau sẽ không có highlight

    # --- Phần hiển thị và xử lý xác nhận cấm --- 
    if st.session_state.pending_ban_edge_info:
        pending_info = st.session_state.pending_ban_edge_info
        st.sidebar.subheader("Xác nhận cấm đoạn:")
        st.sidebar.markdown(f"**Tên:** {pending_info.get('name', 'N/A')}")
        st.sidebar.markdown(f"**OSM ID(s):** {pending_info.get('osmid', 'N/A')}")
        st.sidebar.markdown(f"**Dài:** {pending_info.get('length', 0):.0f}m")

        col_confirm, col_cancel = st.sidebar.columns(2)
        with col_confirm:
            if st.button("✅ Cấm Đoạn Này", key="confirm_ban_pending_btn"):
                ids_to_ban = pending_info['osmid']
                if isinstance(ids_to_ban, list):
                    for oid in ids_to_ban:
                        st.session_state.clicked_banned_osm_ids.add(oid)
                else:
                    st.session_state.clicked_banned_osm_ids.add(ids_to_ban)
                st.toast(f"🚫 Đã cấm: {pending_info.get('name', '')}", icon="🚫")
                st.session_state.pending_ban_edge_info = None # Xóa pending sau khi xử lý
                st.rerun()
        with col_cancel:
            if st.button("❌ Hủy", key="cancel_pending_btn"):
                st.toast(f"Đã hủy cấm đoạn: {pending_info.get('name', '')}", icon="↩️")
                st.session_state.pending_ban_edge_info = None # Xóa pending
                st.rerun()
    # --- Kết thúc phần xác nhận --- 

    st.markdown("---")
    if st.session_state.clicked_banned_osm_ids:
        st.write(f"**Đã cấm {len(st.session_state.clicked_banned_osm_ids)} OSM ID(s) bằng click.**")
        if st.button("Xóa các đường đã cấm bằng Click", key="clear_clicked_bans_btn"):
            st.session_state.clicked_banned_osm_ids = set()
            st.session_state.ban_by_click_mode = False
            st.rerun()
    else:
        st.write("Chưa có đường nào được cấm bằng click.")
    
    if not st.session_state.clicked_banned_osm_ids:
         st.write("Hiện không có đường nào bị cấm.")

    # Hiển thị danh sách gợi ý ở sidebar với checkbox và nút xác nhận
    suggested_colors = ['blue', 'green', 'orange', 'red', 'brown']
    # Lọc các tuyến chưa bị cấm
    filtered_suggested_roads = []
    for idx, road in enumerate(st.session_state.suggested_roads):
        data = road['data']
        osmid = data.get('osmid')
        is_banned = False
        if isinstance(osmid, list):
            if any(oid in st.session_state.clicked_banned_osm_ids for oid in osmid):
                is_banned = True
        elif osmid in st.session_state.clicked_banned_osm_ids:
            is_banned = True
        if not is_banned:
            filtered_suggested_roads.append((idx, road))
    num_suggested = len(filtered_suggested_roads)
    if num_suggested > 0:
        st.sidebar.subheader(f"{num_suggested} tuyến đường gần nhất:")
        selected_indices = []
        for order, (idx, road) in enumerate(filtered_suggested_roads):
            data = road['data']
            name = data.get('name', 'Đường không tên')
            if isinstance(name, list): name = name[0] if name else 'Đường không tên'
            dist = road['distance']*100000
            color = suggested_colors[order % len(suggested_colors)]
            checked = st.session_state.get(f"cb_suggested_{idx}", False)
            cols = st.sidebar.columns([1, 10])
            with cols[0]:
                cb = st.checkbox("", value=checked, key=f"cb_suggested_{idx}")
            with cols[1]:
                st.markdown(
                    f"<span style='color:{color};font-size:18px'>●</span> <b>{name} ({dist:.0f}m)</b>",
                    unsafe_allow_html=True
                )
            if cb:
                selected_indices.append(idx)
        st.sidebar.caption(
            "<span style='color:blue'>●</span> Xanh dương | "
            "<span style='color:green'>●</span> Xanh lá | "
            "<span style='color:orange'>●</span> Cam | "
            "<span style='color:red'>●</span> Đỏ | "
            "<span style='color:brown'>●</span> Nâu",
            unsafe_allow_html=True
        )
        if st.sidebar.button("Xác nhận cấm các tuyến đã chọn"):
            for idx in selected_indices:
                data = st.session_state.suggested_roads[idx]['data']
                osmid = data.get('osmid')
                if isinstance(osmid, list):
                    for oid in osmid:
                        st.session_state.clicked_banned_osm_ids.add(oid)
                else:
                    st.session_state.clicked_banned_osm_ids.add(osmid)
            st.toast("🚫 Đã cấm các tuyến đã chọn!")
            st.session_state.suggested_roads = []
            for idx, _ in filtered_suggested_roads:
                st.session_state.pop(f"cb_suggested_{idx}", None)
            st.rerun()

    # # 2. Thêm nút vào sidebar
    # if st.button("Ẩn/Hiện marker node"):
    #     st.session_state.show_nodes = not st.session_state.show_nodes
    # # st.write(f"Hiển thị marker node: {'Bật' if st.session_state.show_nodes else 'Tắt'}")

G = load_map_data()
places = ["Giảng Võ, Ba Đình, Hà Nội"]
try:
    gdf_districts = ox.geocode_to_gdf(places)
    districts_polygon = gdf_districts.iloc[0]['geometry']
except Exception as e:
    st.error(f"Không thể tải polygon cho phường Giảng Võ: {e}")
    districts_polygon = None

route = None
m = create_map(
    G, 
    st.session_state.points, 
    route, 
    st.session_state.suggested_roads, 
    show_nodes=st.session_state.show_nodes,
    show_edges=st.session_state.show_edges
)

if len(st.session_state.points) == 2:
    start_point_coords, end_point_coords = st.session_state.points
    route = find_shortest_path(G, start_point_coords, end_point_coords)
    m = create_map(
        G, 
        st.session_state.points, 
        route, 
        st.session_state.suggested_roads, 
        show_nodes=st.session_state.show_nodes,
        show_edges=st.session_state.show_edges
    )

map_data = st_folium(m, width=1200, height=600)

if map_data and map_data['last_clicked']:
    lat = map_data['last_clicked']['lat']
    lon = map_data['last_clicked']['lng']
    clicked_point_geom = Point(lon, lat)

    if st.session_state.ban_by_click_mode:
        # Gợi ý 5 tuyến đường gần nhất và lưu vào session_state
        st.session_state.suggested_roads = find_nearest_roads(G, (lon, lat))
        st.rerun()
    else:
        if districts_polygon and districts_polygon.contains(clicked_point_geom):
            st.info("✅ Điểm bạn chọn hợp lệ trong phạm vi phường Giảng Võ.")
            if len(st.session_state.points) < 2:
                st.session_state.points.append((lat, lon))
                st.rerun()
        elif not districts_polygon:
             st.warning("Không thể xác thực điểm nằm trong phường do lỗi tải polygon. Tạm thời chấp nhận điểm.")
             if len(st.session_state.points) < 2:
                st.session_state.points.append((lat, lon))
                st.rerun()
        else:
            st.error("❌ Điểm bạn chọn nằm ngoài phạm vi phường Giảng Võ! Vui lòng chọn lại.")

if st.session_state.clicked_banned_osm_ids:
    st.info(f"Đang cấm bằng click: {len(st.session_state.clicked_banned_osm_ids)} OSM IDs")

if route:
    instructions, total_distance = get_route_instructions(G, route)
    st.success(f"**Tổng quãng đường: {total_distance/1000:.2f} km**")
    st.markdown("### Hướng dẫn chi tiết:")
    for i, instruction in enumerate(instructions, 1):
        st.markdown(f"{i}. {instruction}")
    if st.button("Chọn lại điểm"):
        st.session_state.points = []
        st.session_state.ban_by_click_mode = False
        st.rerun()
else:
    if len(st.session_state.points) == 2:
        st.warning("Không tìm thấy đường đi. Có thể do các đường bị cấm hoặc không có kết nối giữa hai điểm.")

st.markdown("\nCảm ơn bạn đã sử dụng ứng dụng!")