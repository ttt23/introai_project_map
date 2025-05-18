# import osmnx as ox

# G = ox.graph_from_place(
#             ["Đống Đa, Hà Nội, Vietnam", "Ba Đình, Hà Nội, Vietnam"],
#             network_type="all"
#         )
# ox.save_graphml(G, "giang_vo.graphml")
# gdf = ox.geocode_to_gdf("Đống Đa, Hà Nội, Việt Nam")
# print(gdf)

import osmnx as ox
G = ox.graph_from_place('Giảng Võ, Ba Đình, Hà Nội, Vietnam', 
                        network_type='drive')
ox.save_graphml(G, "giang_vo.graphml")
