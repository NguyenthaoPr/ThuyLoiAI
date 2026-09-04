@app.get("/kml-gps-test")
async def kml_gps_test(
    latitude: float,
    longitude: float
):
    """
    Thử nghiệm xác định tuyến LineString gần nhất
    từ một tọa độ GPS.
    """
    # Ưu tiên GIS MASTER KMZ
    if GIS_MASTER_KMZ.exists():
        file_path = GIS_MASTER_KMZ
    else:
        files = sorted(
            KML_DATA_DIR.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        kml_files = [
            p for p in files
            if p.suffix.lower() in {".kml", ".kmz"}
        ]
        if not kml_files:
            return {
                "success": False,
                "message": "Chưa có file KML/KMZ trong hệ thống."
            }
        file_path = kml_files[0]

    print("KML GPS TEST FILE:", file_path)

    try:
        kml_items = parse_kml_kmz(file_path)

        cong_trinh_items, khu_tuoi_items = split_gis_items(kml_items)
        
        lines = [
            item
            for item in cong_trinh_items
            if item.get("construction_type") == "KENH"
            and item.get("geometry_type") == "LineString"
            and item.get("coordinates")
        ]

        if not lines:
            return {
                "success": False,
                "message": "Không tìm thấy tuyến LineString."
            }

        def get_lat_lon(point):
            if not isinstance(point, dict):
                return None, None
            lat = point.get("lat")
            lon = point.get("lon") or point.get("lng")
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                return None, None

        earth_radius = 6371000.0

        def distance_to_segment(gps_lat, gps_lon, lat1, lon1, lat2, lon2):
            ref_lat = math.radians(gps_lat)
            scale_x = earth_radius * math.cos(ref_lat) * math.pi / 180.0
            scale_y = earth_radius * math.pi / 180.0
            x1 = (lon1 - gps_lon) * scale_x
            y1 = (lat1 - gps_lat) * scale_y
            x2 = (lon2 - gps_lon) * scale_x
            y2 = (lat2 - gps_lat) * scale_y
            dx = x2 - x1
            dy = y2 - y1
            segment_length_sq = dx*dx + dy*dy
            if segment_length_sq == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, -(x1*dx + y1*dy) / segment_length_sq))
            nearest_x = x1 + t * dx
            nearest_y = y1 + t * dy
            distance = math.sqrt(nearest_x*nearest_x + nearest_y*nearest_y)
            nearest_lat = gps_lat + nearest_y / scale_y
            nearest_lon = gps_lon + nearest_x / scale_x
            segment_length = math.sqrt(segment_length_sq)
            return distance, nearest_lat, nearest_lon, t, segment_length

        results = []
        for item in lines:
            coordinates = item.get("coordinates", [])
            cumulative_distance = 0.0
            best_distance = None
            best_lat = best_lon = None
            best_ratio = None
            best_segment_length = None
            best_distance_along_line = None

            for i in range(len(coordinates) - 1):
                lat1, lon1 = get_lat_lon(coordinates[i])
                lat2, lon2 = get_lat_lon(coordinates[i+1])
                if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
                    continue
                dist, nlat, nlon, ratio, seg_len = distance_to_segment(
                    latitude, longitude, lat1, lon1, lat2, lon2
                )
                if best_distance is None or dist < best_distance:
                    best_distance = dist
                    best_lat = nlat
                    best_lon = nlon
                    best_ratio = ratio
                    best_segment_length = seg_len
                    best_distance_along_line = cumulative_distance + ratio * seg_len
                cumulative_distance += seg_len

            if best_distance is not None:
                results.append({
                    "name": item.get("name", ""),
                    "geometry_type": "LineString",
                    "coordinate_count": len(coordinates),
                    "distance_m": round(best_distance, 2),
                    "distance_along_line_m": round(best_distance_along_line, 2),
                    "nearest_point": {
                        "latitude": round(best_lat, 8),
                        "longitude": round(best_lon, 8)
                    }
                })

        results.sort(key=lambda x: x["distance_m"])

        # Đánh giá khoảng cách
        for item in results:
            d = item["distance_m"]
            if d <= 20:
                item["status"] = "RẤT GẦN"
                item["status_code"] = "GREEN"
                item["assessment"] = "Có thể xác nhận vị trí trên tuyến."
            elif d <= 50:
                item["status"] = "GẦN"
                item["status_code"] = "YELLOW"
                item["assessment"] = "Gần tuyến, cần kiểm tra thực tế."
            elif d <= 100:
                item["status"] = "XA"
                item["status_code"] = "ORANGE"
                item["assessment"] = "Khoảng cách lớn, cần kiểm tra lại GPS."
            else:
                item["status"] = "NGOÀI PHẠM VI"
                item["status_code"] = "RED"
                item["assessment"] = "Không đủ cơ sở xác nhận vị trí trên tuyến."

        # Xác định GIS từ kết quả
        gis_identification = None

        if results:
            sorted_results = sorted(results, key=lambda x: x.get("distance_m", 999999))
            nearest = sorted_results[0]
            nearest_distance = nearest.get("distance_m", 999999)

            if nearest_distance <= 50:
                gis_identification = {
                    "identified": True,
                    "name": nearest.get("name", ""),
                    "geometry_type": nearest.get("geometry_type", "LineString"),
                    "construction_type": "KENH",
                    "distance_m": nearest_distance,
                    "nearest_point": nearest.get("nearest_point"),
                    "status": nearest.get("status", "CHƯA XÁC ĐỊNH"),
                    "status_code": nearest.get("status_code", "RED"),
                    "distance_along_line_m": nearest.get("distance_along_line_m"),
                    "assessment": nearest.get("assessment", ""),
                    "source": "GIS MASTER KMZ"
                }
            else:
                gis_identification = {
                    "identified": False,
                    "name": "",
                    "geometry_type": None,
                    "construction_type": None,
                    "distance_m": nearest_distance,
                    "nearest_point": nearest.get("nearest_point"),
                    "status": "NGOÀI PHẠM VI",
                    "status_code": "RED",
                    "distance_along_line_m": None,
                    "assessment": (
                        f"GPS cách tuyến kênh gần nhất {nearest_distance:.1f} m, "
                        "không đủ cơ sở xác định công trình."
                    ),
                    "source": "GIS MASTER KMZ"
                }

        # === TRẢ VỀ KẾT QUẢ CHO MỌI TRƯỜNG HỢP ===
        return {
            "success": True,
            "file": file_path.name,
            "gps": {"latitude": latitude, "longitude": longitude},
            "gis_identification": gis_identification,
            "linestring_count": len(lines),
            "nearest": results[:10] if results else [],
            "message": "Đã kiểm tra GPS với hệ thống tuyến LineString."
        }

    except Exception as e:
        print("[KML GPS TEST ERROR]", repr(e))
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi kiểm tra GPS: {str(e)}"
        )
