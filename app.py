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
from geopy.distance import geodesic

st.set_page_config(page_title="Bản đồ chỉ đường Giảng Võ - Ba Đình", layout="wide")
st.title("Bản đồ chỉ đường Giảng Võ - Ba Đình")

# Cập nhật vị trí trung tâm chính xác hơn cho phường Giảng Võ
CENTER = [21.0286, 105.8232]  # Vị trí trung tâm Hồ Giảng Võ

# Tạo danh sách các địa điểm quan trọng trong phường Giảng Võ
# Xóa danh sách LANDMARKS theo yêu cầu

def meters_to_degrees(meters):
    # 1 độ ~ 111_320 mét (ở xích đạo), dùng gần đúng cho Hà Nội
    return meters / 111320

def is_point_in_circle(point, circle_center, radius_m):
    if not circle_center or not radius_m:
        return False
    from geopy.distance import geodesic
    return geodesic(point, circle_center).meters <= radius_m

@st.cache_data
def load_roads():
    with open('roads.json', 'r', encoding='utf-8') as f:
        return json.load(f)

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

# Session state cho tính năng cấm đường theo node
if 'ban_by_node_mode' not in st.session_state:
    st.session_state.ban_by_node_mode = False
if 'selected_nodes' not in st.session_state:
    st.session_state.selected_nodes = []
if 'current_node' not in st.session_state:
    st.session_state.current_node = None
if 'adjacent_nodes' not in st.session_state:
    st.session_state.adjacent_nodes = []
if 'banned_edges_by_nodes' not in st.session_state:
    st.session_state.banned_edges_by_nodes = set()
if 'banned_osmids_by_nodes' not in st.session_state:
    st.session_state.banned_osmids_by_nodes = set()

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

# Kiểm tra thay đổi checkbox ban_by_circle_mode
if 'ban_by_circle_mode_prev' not in st.session_state:
    st.session_state.ban_by_circle_mode_prev = False

current_ban_by_circle_mode = st.session_state.get('ban_by_circle_mode', False)
ban_mode_changed = current_ban_by_circle_mode != st.session_state.ban_by_circle_mode_prev
st.session_state.ban_by_circle_mode_prev = current_ban_by_circle_mode

# Khi tắt chế độ cấm theo vùng, xóa vùng cấm và làm mới bản đồ ngay lập tức
if ban_mode_changed and not current_ban_by_circle_mode:
    # Xóa các OSM ID đã cấm bởi vùng cấm
    prev_banned = st.session_state.get('banned_osmids_by_circle', set())
    st.session_state.clicked_banned_osm_ids.difference_update(prev_banned)
    # Xóa các edge bị cấm bởi vùng cấm trước đó
    prev_banned_edges = st.session_state.get('banned_edges_by_circle', set())
    st.session_state.banned_edges_by_circle.difference_update(prev_banned_edges)
    st.session_state.banned_osmids_by_circle = set()
    # Xóa điểm trung tâm và bán kính vùng cấm
    st.session_state.last_circle_ban_center = None
    st.session_state.last_circle_ban_radius = None
    # Làm mới bản đồ ngay lập tức
    st.rerun()

# Thêm input nhập bán kính và checkbox bật chế độ cấm theo vùng tròn ở sidebar
with st.sidebar:
    st.header("Hiển thị")
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
    
    # Thêm phần cấm đường theo node
    st.subheader("Cấm đường theo node")
    st.session_state.ban_by_node_mode = st.checkbox(
        "Kích hoạt chế độ cấm theo node", 
        value=st.session_state.get('ban_by_node_mode', False),
        key="cb_ban_by_node_mode"
    )
    
    # Hiển thị thông tin về các node đã chọn
    if st.session_state.ban_by_node_mode:
        if st.session_state.selected_nodes:
            st.write(f"Đã chọn {len(st.session_state.selected_nodes)} node")
            
            if len(st.session_state.selected_nodes) >= 2:
                if st.button("Cấm dãy node đã chọn", key="ban_selected_nodes"):
                    # Lưu các cạnh giữa các node liền kề trong dãy
                    G = load_map_data()  # Đảm bảo G đã được tải
                    
                    # Tìm đường đi ngắn nhất giữa các node liên tiếp để đảm bảo đường cấm được kín
                    for i in range(len(st.session_state.selected_nodes) - 1):
                        u = st.session_state.selected_nodes[i]
                        v = st.session_state.selected_nodes[i + 1]
                        
                        # Kiểm tra xem có đường đi trực tiếp giữa u và v không
                        if G.has_edge(u, v):
                            # Cấm tất cả các cạnh trực tiếp giữa u và v
                            for k, data in G[u][v].items():
                                st.session_state.banned_edges_by_nodes.add((u, v, k))
                                osmid = data.get('osmid')
                                if isinstance(osmid, list):
                                    for oid in osmid:
                                        st.session_state.banned_osmids_by_nodes.add(oid)
                                        st.session_state.clicked_banned_osm_ids.add(oid)
                                else:
                                    st.session_state.banned_osmids_by_nodes.add(osmid)
                                    st.session_state.clicked_banned_osm_ids.add(osmid)
                        else:
                            # Nếu không có đường trực tiếp, tìm đường đi ngắn nhất giữa u và v
                            try:
                                path = nx.shortest_path(G, u, v, weight='length')
                                # Cấm tất cả các cạnh trên đường đi
                                for j in range(len(path) - 1):
                                    path_u = path[j]
                                    path_v = path[j + 1]
                                    for k, data in G[path_u][path_v].items():
                                        st.session_state.banned_edges_by_nodes.add((path_u, path_v, k))
                                        osmid = data.get('osmid')
                                        if isinstance(osmid, list):
                                            for oid in osmid:
                                                st.session_state.banned_osmids_by_nodes.add(oid)
                                                st.session_state.clicked_banned_osm_ids.add(oid)
                                        else:
                                            st.session_state.banned_osmids_by_nodes.add(osmid)
                                            st.session_state.clicked_banned_osm_ids.add(osmid)
                            except nx.NetworkXNoPath:
                                st.warning(f"Không tìm thấy đường đi giữa node {u} và {v}")
                    
                    st.toast(f"🚫 Đã cấm {len(st.session_state.banned_edges_by_nodes)} đoạn đường giữa các node!", icon="🚫")
                    st.session_state.selected_nodes = []
                    st.session_state.current_node = None
                    st.session_state.adjacent_nodes = []
                    st.rerun()
            
            if st.button("Xóa lựa chọn", key="clear_selected_nodes"):
                st.session_state.selected_nodes = []
                st.session_state.current_node = None
                st.session_state.adjacent_nodes = []
                st.rerun()
        
        # Hiển thị các đoạn đã cấm theo node
        if st.session_state.banned_osmids_by_nodes:
            st.write(f"**Đã cấm {len(st.session_state.banned_osmids_by_nodes)} OSM ID(s) bằng node.**")
            if st.button("Xóa các đường đã cấm bằng Node", key="clear_node_bans_btn"):
                # Xóa khỏi danh sách cấm chính
                st.session_state.clicked_banned_osm_ids.difference_update(st.session_state.banned_osmids_by_nodes)
                st.session_state.banned_edges_by_nodes = set()
                st.session_state.banned_osmids_by_nodes = set()
                st.toast("✅ Đã xóa tất cả các đường cấm bằng node!", icon="✅")
                st.rerun()

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

    st.header("Cấm đường theo vùng tròn")
    st.session_state.ban_by_circle_mode = st.checkbox(
        "Kích hoạt chế độ cấm theo vùng", 
        value=st.session_state.get('ban_by_circle_mode', False), 
        key="cb_ban_by_circle_mode"
    )
    st.session_state.circle_ban_radius = st.number_input(
        "Nhập bán kính vùng cấm (mét)", min_value=10, max_value=1000, value=100, step=10, key="circle_ban_radius_input"
    )

# --- KẾT THÚC KHỞI TẠO SESSION STATE ---

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

def is_segment_restricted(G, u, v, k=None):
    # Kiểm tra thuộc tính cấm trực tiếp trên edge
    if k is not None and G.has_edge(u, v, k):
        if G[u][v][k].get('banned_by_circle', False):
            return True
    banned_by_click = st.session_state.get('clicked_banned_osm_ids', set())
    banned_edges_by_circle = st.session_state.get('banned_edges_by_circle', set())
    banned_edges_by_nodes = st.session_state.get('banned_edges_by_nodes', set())
    
    # Kiểm tra cạnh có bị cấm bởi node không
    if k is not None and (u, v, k) in banned_edges_by_nodes:
        return True
        
    # Kiểm tra cạnh có bị cấm bởi vùng tròn không
    if k is not None and (u, v, k) in banned_edges_by_circle:
        return True
        
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
    # Tạo bản sao của graph để không ảnh hưởng tới graph gốc
    G_copy = G.copy()
    
    # Xóa tất cả các edge bị cấm (trong vùng cấm) khỏi graph tạm thời
    edges_to_remove = []
    for u, v, k, data in G.edges(data=True, keys=True):
        # Kiểm tra các cạnh bị cấm theo vùng tròn
        if data.get('banned_by_circle', False) or (u, v, k) in st.session_state.get('banned_edges_by_circle', set()):
            edges_to_remove.append((u, v, k))
        
        # Kiểm tra các cạnh bị cấm theo node
        if (u, v, k) in st.session_state.get('banned_edges_by_nodes', set()):
            edges_to_remove.append((u, v, k))
        
        # Kiểm tra các cạnh bị cấm theo OSM ID
        osmid = data.get('osmid')
        if osmid:
            if isinstance(osmid, list):
                if any(oid in st.session_state.clicked_banned_osm_ids for oid in osmid):
                    edges_to_remove.append((u, v, k))
            elif osmid in st.session_state.clicked_banned_osm_ids:
                edges_to_remove.append((u, v, k))
    
    for u, v, k in edges_to_remove:
        if G_copy.has_edge(u, v, k):
            G_copy.remove_edge(u, v, k)
    
    # Tìm đường trên graph đã lọc (không có các đoạn bị cấm)
    start_node = ox.nearest_nodes(G_copy, start_point[1], start_point[0])
    end_node = ox.nearest_nodes(G_copy, end_point[1], end_point[0])
    
    try:
        route = nx.astar_path(G_copy, start_node, end_node, weight=lambda u, v, d: d.get('length', 1))
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

def create_map(G, points=None, route=None, suggested_roads=None, show_nodes=False, show_edges=False, circle_ban_center=None, circle_ban_radius=None):
    m = folium.Map(location=CENTER, zoom_start=15)
    
    # Vẽ ranh giới phường Giảng Võ rõ ràng, không fill, không hiệu ứng
    if 'districts_polygon' in globals() and districts_polygon is not None:
        district_coords = []
        if hasattr(districts_polygon, 'exterior'):
            district_coords = [(y, x) for x, y in districts_polygon.exterior.coords]
        elif hasattr(districts_polygon, 'geoms'):
            district_coords = [(y, x) for x, y in list(districts_polygon.geoms)[0].exterior.coords]
        
        if district_coords:
            folium.PolyLine(
                locations=district_coords,
                color='green',
                weight=4,
                opacity=1,
                dash_array='5, 10',
                popup="Ranh giới phường Giảng Võ"
            ).add_to(m)
    
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
            
    # Vẽ các cạnh đã bị cấm bởi node (màu đỏ đậm)
    if st.session_state.banned_edges_by_nodes:
        for u, v, k in st.session_state.banned_edges_by_nodes:
            if G.has_edge(u, v, k):
                data = G[u][v][k]
                if 'geometry' in data:
                    coords = [(coord[1], coord[0]) for coord in data['geometry'].coords]
                    folium.PolyLine(coords, weight=7, color='darkred', opacity=0.9,
                                    popup=f"Đã cấm bằng node (OSM ID: {data.get('osmid', 'N/A')})").add_to(m)
                else:
                    # Nếu không có geometry, vẽ đường thẳng
                    u_lat, u_lon = G.nodes[u]['y'], G.nodes[u]['x']
                    v_lat, v_lon = G.nodes[v]['y'], G.nodes[v]['x']
                    folium.PolyLine([[u_lat, u_lon], [v_lat, v_lon]], 
                                    weight=7, color='darkred', opacity=0.9,
                                    popup=f"Đã cấm bằng node (OSM ID: {data.get('osmid', 'N/A')})").add_to(m)
    
    # 1. Vẽ các cạnh đã bị cấm chính thức (màu tím)
    if st.session_state.clicked_banned_osm_ids:
        for u_b, v_b, data_b in G.edges(data=True):
            osmid_b = data_b.get('osmid')
            is_officially_banned = False
            is_banned_by_node = False
            
            # Kiểm tra xem đoạn đường này có bị cấm không
            if osmid_b:
                if isinstance(osmid_b, list):
                    if any(oid in st.session_state.clicked_banned_osm_ids for oid in osmid_b):
                        is_officially_banned = True
                        # Kiểm tra xem có bị cấm bởi node không
                        if any(oid in st.session_state.banned_osmids_by_nodes for oid in osmid_b):
                            is_banned_by_node = True
                elif osmid_b in st.session_state.clicked_banned_osm_ids:
                    is_officially_banned = True
                    if osmid_b in st.session_state.banned_osmids_by_nodes:
                        is_banned_by_node = True
            
            # Chỉ vẽ các đoạn không phải bị cấm bởi node (vì đã vẽ ở trên)
            if is_officially_banned and not is_banned_by_node and 'geometry' in data_b:
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
    
    # Vẽ các node và đường nối cho tính năng cấm theo node
    if st.session_state.ban_by_node_mode:
        # Vẽ tất cả các node nếu chưa chọn node nào
        if not st.session_state.selected_nodes:
            for node_id, data in G.nodes(data=True):
                lat = data['y']
                lon = data['x']
                folium.CircleMarker(
                    [lat, lon],
                    radius=5,
                    color='blue',
                    fill=True,
                    fill_color='blue',
                    fill_opacity=0.7,
                    popup=f"Node {node_id}",
                    tooltip=f"Node {node_id}"
                ).add_to(m)
        
        # Vẽ các node đã chọn
        for i, node_id in enumerate(st.session_state.selected_nodes):
            node_data = G.nodes[node_id]
            lat = node_data['y']
            lon = node_data['x']
            folium.CircleMarker(
                [lat, lon],
                radius=8,
                color='red',
                fill=True,
                fill_color='red',
                fill_opacity=0.7,
                popup=f"Node {node_id} (Đã chọn {i+1})",
                tooltip=f"Node {node_id} (Đã chọn {i+1})"
            ).add_to(m)
            
            # Vẽ đường nối giữa các node đã chọn
            if i > 0:
                prev_node_id = st.session_state.selected_nodes[i-1]
                prev_node_data = G.nodes[prev_node_id]
                prev_lat = prev_node_data['y']
                prev_lon = prev_node_data['x']
                
                # Kiểm tra xem có cạnh trực tiếp giữa hai node không
                if G.has_edge(prev_node_id, node_id):
                    # Lấy dữ liệu của cạnh
                    edge_data = G.get_edge_data(prev_node_id, node_id)
                    if edge_data:
                        # Nếu có nhiều cạnh giữa hai node, lấy cạnh đầu tiên
                        key = list(edge_data.keys())[0]
                        data = edge_data[key]
                        
                        # Nếu có geometry, vẽ theo geometry
                        if 'geometry' in data:
                            coords = [(coord[1], coord[0]) for coord in data['geometry'].coords]
                            folium.PolyLine(coords, weight=5, color='green', opacity=0.9,
                                           popup=f"Đường nối đã chọn").add_to(m)
                        else:
                            # Nếu không có geometry, vẽ đường thẳng
                            folium.PolyLine([[prev_lat, prev_lon], [lat, lon]], 
                                           weight=5, color='green', opacity=0.9,
                                           popup=f"Đường nối đã chọn").add_to(m)
        
        # Vẽ các node liền kề với node cuối cùng đã chọn
        if st.session_state.selected_nodes and st.session_state.adjacent_nodes:
            current_node_id = st.session_state.selected_nodes[-1]
            current_node_data = G.nodes[current_node_id]
            current_lat = current_node_data['y']
            current_lon = current_node_data['x']
            
            for adj_node_id in st.session_state.adjacent_nodes:
                adj_node_data = G.nodes[adj_node_id]
                adj_lat = adj_node_data['y']
                adj_lon = adj_node_data['x']
                
                # Vẽ node liền kề
                folium.CircleMarker(
                    [adj_lat, adj_lon],
                    radius=6,
                    color='orange',
                    fill=True,
                    fill_color='orange',
                    fill_opacity=0.7,
                    popup=f"Node {adj_node_id} (Liền kề)",
                    tooltip=f"Node {adj_node_id} (Liền kề)"
                ).add_to(m)
                
                # Vẽ đường nối đến node liền kề
                if G.has_edge(current_node_id, adj_node_id):
                    edge_data = G.get_edge_data(current_node_id, adj_node_id)
                    if edge_data:
                        key = list(edge_data.keys())[0]
                        data = edge_data[key]
                        
                        if 'geometry' in data:
                            coords = [(coord[1], coord[0]) for coord in data['geometry'].coords]
                            folium.PolyLine(coords, weight=4, color='orange', opacity=0.9,
                                           popup=f"Đường nối đến node liền kề").add_to(m)
                        else:
                            folium.PolyLine([[current_lat, current_lon], [adj_lat, adj_lon]], 
                                           weight=4, color='orange', opacity=0.9,
                                           popup=f"Đường nối đến node liền kề").add_to(m)
    
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
    # Vẽ vùng cấm nếu có
    if circle_ban_center and circle_ban_radius:
        folium.Circle(
            location=[circle_ban_center[0], circle_ban_center[1]],
            radius=circle_ban_radius, # mét
            color='red',
            fill=True,
            fill_color='#ff6666',
            fill_opacity=0.25,
            opacity=0.5,
            popup='Vùng cấm'
        ).add_to(m)
        folium.Marker(
            location=[circle_ban_center[0], circle_ban_center[1]],
            icon=folium.Icon(color='red', icon='ban', prefix='fa'),
            popup='Tâm vùng cấm'
        ).add_to(m)
    return m

G = load_map_data()
places = ["Giảng Võ, Ba Đình, Hà Nội"]

# Xóa thuộc tính banned_by_circle trên toàn bộ graph nếu cần
if ban_mode_changed and not current_ban_by_circle_mode:
    for u, v, k, data in G.edges(data=True, keys=True):
        if 'banned_by_circle' in data:
            data['banned_by_circle'] = False

# Hiển thị thông báo giới thiệu khi ứng dụng mới khởi động
if 'app_first_run' not in st.session_state:
    st.session_state.app_first_run = True
    # Sử dụng info box để hiển thị thông tin phường Giảng Võ
    st.info("""
    **Chào mừng đến với Bản đồ chỉ đường Phường Giảng Võ - Ba Đình!**
    
    Bản đồ hiển thị khu vực Phường Giảng Võ với các tính năng:
    - Tìm đường đi giữa hai điểm
    - Cấm đường theo node, click hoặc vùng
    
    Để bắt đầu, hãy click vào bản đồ để chọn điểm bắt đầu và điểm kết thúc, hoặc sử dụng các tính năng cấm đường ở thanh bên trái.
    """)
            
# Lấy ranh giới phường Giảng Võ
try:
    # Truy vấn cụ thể hơn cho phường Giảng Võ
    gdf_districts = ox.geocode_to_gdf("Giảng Võ, Ba Đình, Hà Nội, Việt Nam")
    districts_polygon = gdf_districts.iloc[0]['geometry']
    st.success("Đã tải ranh giới phường Giảng Võ thành công!")
except Exception as e:
    st.error(f"Không thể tải polygon cho phường Giảng Võ: {e}")
    districts_polygon = None

# Nếu polygon vẫn là None, thử cách khác
if districts_polygon is None:
    try:
        # Tạo một polygon gần đúng cho khu vực Giảng Võ
        center_point = Point(105.8232, 21.0286)  # Tọa độ trung tâm Hồ Giảng Võ
        buffer_distance = 0.005  # Khoảng 500m
        districts_polygon = center_point.buffer(buffer_distance)
        st.warning("Sử dụng ranh giới ước lượng cho phường Giảng Võ")
    except Exception as e:
        st.error(f"Không thể tạo polygon ước lượng: {e}")
        districts_polygon = None

route = None
m = create_map(
    G, 
    st.session_state.points, 
    route, 
    st.session_state.suggested_roads, 
    show_nodes=st.session_state.show_nodes,
    show_edges=st.session_state.show_edges,
    circle_ban_center=st.session_state.get('last_circle_ban_center'),
    circle_ban_radius=st.session_state.get('last_circle_ban_radius')
)

if len(st.session_state.points) == 2:
    start_point_coords, end_point_coords = st.session_state.points
    # Chỉ kiểm tra vùng cấm nếu đang bật chế độ cấm theo vùng
    if st.session_state.get('ban_by_circle_mode', False):
        circle_center = st.session_state.get('last_circle_ban_center')
        circle_radius = st.session_state.get('last_circle_ban_radius')
        in_ban_start = is_point_in_circle(start_point_coords, circle_center, circle_radius)
        in_ban_end = is_point_in_circle(end_point_coords, circle_center, circle_radius)
        if in_ban_start or in_ban_end:
            route = None
            st.warning("Không có đường đi thỏa mãn (điểm đi hoặc đến nằm trong vùng cấm)")
        else:
            route = find_shortest_path(G, start_point_coords, end_point_coords)
    else:
        route = find_shortest_path(G, start_point_coords, end_point_coords)
    m = create_map(
        G, 
        st.session_state.points, 
        route, 
        st.session_state.suggested_roads, 
        show_nodes=st.session_state.show_nodes,
        show_edges=st.session_state.show_edges,
        circle_ban_center=st.session_state.get('last_circle_ban_center'),
        circle_ban_radius=st.session_state.get('last_circle_ban_radius')
    )

map_data = st_folium(m, width=1200, height=600)

# Thêm nút reset bản đồ để hiển thị toàn bộ phường Giảng Võ
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("Phóng bản đồ về Giảng Võ", key="reset_map_btn"):
        # Xóa các điểm đã chọn
        st.session_state.points = []
        # Tắt các chế độ cấm
        st.session_state.ban_by_node_mode = False
        st.session_state.ban_by_click_mode = False
        st.session_state.ban_by_circle_mode = False
        # Làm mới bản đồ
        st.rerun()

if map_data and map_data['last_clicked']:
    lat = map_data['last_clicked']['lat']
    lon = map_data['last_clicked']['lng']
    clicked_point_geom = Point(lon, lat)

    # Xử lý khi tắt chế độ cấm theo vùng: xóa điểm chọn và vùng cấm
    if not st.session_state.get('ban_by_circle_mode', False):
        prev_banned = st.session_state.get('banned_osmids_by_circle', set())
        st.session_state.clicked_banned_osm_ids.difference_update(prev_banned)
        # Xóa các edge bị cấm bởi vùng cấm trước đó
        prev_banned_edges = st.session_state.get('banned_edges_by_circle', set())
        st.session_state.banned_edges_by_circle.difference_update(prev_banned_edges)
        st.session_state.banned_osmids_by_circle = set()
        st.session_state.last_circle_ban_center = None
        st.session_state.last_circle_ban_radius = None
        # Xóa thuộc tính banned_by_circle trên toàn bộ graph
        for u, v, k, data in G.edges(data=True, keys=True):
            if 'banned_by_circle' in data:
                data['banned_by_circle'] = False
    
    # Xử lý khi bật chế độ cấm theo node
    if st.session_state.get('ban_by_node_mode', False):
        # Tìm node gần nhất với điểm click
        nearest_node = ox.nearest_nodes(G, lon, lat)
        
        # Nếu chưa có node nào được chọn, thêm node đầu tiên
        if not st.session_state.selected_nodes:
            st.session_state.selected_nodes.append(nearest_node)
            st.session_state.current_node = nearest_node
            
            # Tìm các node liền kề với node hiện tại
            st.session_state.adjacent_nodes = list(G.neighbors(nearest_node))
            # Loại bỏ các node đã chọn khỏi danh sách liền kề
            st.session_state.adjacent_nodes = [n for n in st.session_state.adjacent_nodes if n not in st.session_state.selected_nodes]
            
            st.toast(f"Đã chọn node {nearest_node}. Chọn một node liền kề để tạo đường cấm.", icon="✅")
            st.rerun()
        else:
            # Kiểm tra xem node vừa click có phải là node liền kề với node cuối cùng không
            current_node = st.session_state.selected_nodes[-1]
            
            # Nếu click vào chính node hiện tại, không làm gì cả
            if nearest_node == current_node:
                st.toast("Bạn đã click vào node hiện tại. Hãy chọn một node liền kề.", icon="ℹ️")
                st.rerun()
            
            # Nếu node vừa click nằm trong danh sách node liền kề
            elif nearest_node in st.session_state.adjacent_nodes:
                # Thêm node vào danh sách đã chọn
                st.session_state.selected_nodes.append(nearest_node)
                st.session_state.current_node = nearest_node
                
                # Cập nhật danh sách node liền kề mới
                st.session_state.adjacent_nodes = list(G.neighbors(nearest_node))
                # Loại bỏ các node đã chọn khỏi danh sách liền kề
                st.session_state.adjacent_nodes = [n for n in st.session_state.adjacent_nodes if n not in st.session_state.selected_nodes]
                
                # Hiển thị thông báo với thông tin chi tiết
                node_count = len(st.session_state.selected_nodes)
                adjacent_count = len(st.session_state.adjacent_nodes)
                
                if adjacent_count > 0:
                    st.toast(f"Đã chọn node {nearest_node}. Tổng cộng: {node_count} node. Có {adjacent_count} node liền kề khả dụng.", icon="✅")
                else:
                    st.toast(f"Đã chọn node {nearest_node}. Tổng cộng: {node_count} node. Không còn node liền kề khả dụng.", icon="⚠️")
                
                st.rerun()
            else:
                # Tìm node gần nhất trong danh sách các node liền kề
                if st.session_state.adjacent_nodes:
                    # Tính khoảng cách từ điểm click đến các node liền kề
                    distances = []
                    for adj_node in st.session_state.adjacent_nodes:
                        adj_lat = G.nodes[adj_node]['y']
                        adj_lon = G.nodes[adj_node]['x']
                        dist = ((adj_lat - lat)**2 + (adj_lon - lon)**2)**0.5
                        distances.append((adj_node, dist))
                    
                    # Sắp xếp theo khoảng cách và lấy node gần nhất
                    distances.sort(key=lambda x: x[1])
                    nearest_adj_node, nearest_dist = distances[0]
                    
                    st.toast(f"Node bạn chọn không liền kề. Node liền kề gần nhất là {nearest_adj_node}.", icon="❌")
                else:
                    st.toast("Node bạn chọn không liền kề và không còn node liền kề nào khả dụng.", icon="❌")
                st.rerun()

    # Nếu bật chế độ cấm theo vùng tròn
    elif st.session_state.get('ban_by_circle_mode', False):
        radius = st.session_state.get('circle_ban_radius', 100)
        radius_deg = meters_to_degrees(radius)
        circle = clicked_point_geom.buffer(radius_deg)
        banned_osmids = set()
        banned_edges_by_circle = set()
        for u, v, k, data in G.edges(data=True, keys=True):
            geom = data.get('geometry')
            if geom and circle.intersects(geom):
                osmid = data.get('osmid')
                if isinstance(osmid, list):
                    banned_osmids.update(osmid)
                else:
                    banned_osmids.add(osmid)
                banned_edges_by_circle.add((u, v, k))
                # Gán thuộc tính cấm trực tiếp vào edge
                data['banned_by_circle'] = True
            else:
                data['banned_by_circle'] = False
        # Xóa các OSM ID đã cấm bởi vùng cấm trước đó
        prev_banned = st.session_state.get('banned_osmids_by_circle', set())
        st.session_state.clicked_banned_osm_ids.difference_update(prev_banned)
        # Xóa các edge bị cấm bởi vùng cấm trước đó
        prev_banned_edges = st.session_state.get('banned_edges_by_circle', set())
        st.session_state.banned_edges_by_circle.difference_update(prev_banned_edges)
        # Cập nhật OSM ID và edge mới bị cấm bởi vùng cấm mới
        st.session_state.clicked_banned_osm_ids.update(banned_osmids)
        st.session_state.banned_osmids_by_circle = banned_osmids
        st.session_state.banned_edges_by_circle.update(banned_edges_by_circle)
        st.session_state.last_circle_ban_center = (lat, lon)
        st.session_state.last_circle_ban_radius = radius
        st.success(f"Đã cấm {len(banned_osmids)} đoạn đường trong bán kính {radius} mét!")
        st.rerun()
    # Xử lý chế độ cấm bằng click
    elif st.session_state.get('ban_by_click_mode', False):
        # Tìm đoạn đường gần nhất với điểm click
        nearest_roads = find_nearest_roads(G, (lon, lat), num_roads=1, max_distance=0.001)
        if nearest_roads:
            road = nearest_roads[0]
            data = road['data']
            osmid = data.get('osmid')
            name = data.get('name', 'Đường không tên')
            if isinstance(name, list): name = name[0] if name else 'Đường không tên'
            
            # Lưu thông tin đoạn đường đang chờ xác nhận cấm
            st.session_state.pending_ban_edge_info = {
                'u': road['u'],
                'v': road['v'],
                'key': road['key'],
                'osmid': osmid,
                'name': name,
                'geometry': data.get('geometry'),
                'length': data.get('length', 0)
            }
            st.toast(f"Đã chọn đoạn: {name}. Xác nhận cấm ở sidebar.", icon="ℹ️")
            st.rerun()
        else:
            st.toast("Không tìm thấy đoạn đường nào gần điểm bạn chọn.", icon="❌")
            st.rerun()
    else:
        # Khi tắt chế độ cấm theo vùng, chỉ cho phép chọn điểm trong phường Giảng Võ
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

# Thêm biến lưu điểm và bán kính vùng cấm vào session_state
if 'last_circle_ban_center' not in st.session_state:
    st.session_state.last_circle_ban_center = None
if 'last_circle_ban_radius' not in st.session_state:
    st.session_state.last_circle_ban_radius = None
# Thêm biến lưu OSM ID bị cấm bởi vùng cấm vào session_state
if 'banned_osmids_by_circle' not in st.session_state:
    st.session_state.banned_osmids_by_circle = set()
# Thêm biến lưu các edge bị cấm bởi vùng cấm vào session_state
if 'banned_edges_by_circle' not in st.session_state:
    st.session_state.banned_edges_by_circle = set()