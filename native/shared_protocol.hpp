#pragma once

#include <cstddef>
#include <cstdint>

namespace atrbridge {

constexpr char kMagic[8] = {'A', 'T', 'R', 'B', 'V', '2', '\0', '\0'};
constexpr std::uint32_t kProtocolVersion = 2;
constexpr std::size_t kHeaderRegionBytes = 4096;
constexpr std::size_t kImageSlotMetaBytes = 65536;
constexpr std::size_t kImageSlotCount = 3;
constexpr std::size_t kMaxImageBytes = 8 * 1024 * 1024;
constexpr std::size_t kMaxRawPoints = 256;
constexpr std::size_t kMaxFiducials = 256;
constexpr std::size_t kMaxMarkers = 128;
constexpr std::size_t kPoseRingCapacity = 512;

enum class BridgeState : std::uint32_t {
    Starting = 1,
    Running = 2,
    Reconnecting = 3,
    Failed = 4,
    Stopping = 5,
};

#pragma pack(push, 1)
struct SharedHeader {
    char magic[8];
    std::uint32_t version;
    std::uint32_t header_bytes;
    std::uint64_t total_bytes;
    std::uint32_t process_id;
    std::uint32_t state;
    std::uint32_t active_slot;
    std::uint32_t reserved0;
    std::int64_t published_sequence;
    std::int64_t pose_write_sequence;
    std::uint64_t pose_overwrites;
    std::uint64_t native_dropped_frames;
    std::uint64_t heartbeat_ns;
    std::uint64_t serial_number;
    std::int32_t device_type;
    char sdk_version[64];
    char last_error[512];
};

struct PointRecord {
    std::uint32_t index;
    std::int32_t geometry_id;
    float x;
    float y;
};

struct FiducialRecord {
    std::uint32_t index;
    std::int32_t left_index;
    std::int32_t right_index;
    std::uint32_t status;
    float x_mm;
    float y_mm;
    float z_mm;
    float left_x;
    float left_y;
    float right_x;
    float right_y;
    float epipolar_error;
    float triangulation_error;
    float probability;
};

struct MarkerRecord {
    std::uint32_t geometry_id;
    std::uint32_t tracking_id;
    std::uint32_t status;
    std::uint32_t reserved0;
    std::uint64_t presence_mask;
    float rotation[9];
    float translation_mm[3];
    float registration_error_mm;
};

struct ImageSlotMeta {
    std::uint32_t state;
    std::uint32_t reserved0;
    std::int64_t sequence;
    std::uint32_t frame_counter;
    std::uint32_t width;
    std::uint32_t height;
    std::uint32_t pixel_format;
    std::int32_t stride_bytes;
    std::uint32_t left_bytes;
    std::uint32_t right_bytes;
    std::uint32_t left_point_count;
    std::uint32_t right_point_count;
    std::uint32_t fiducial_count;
    std::uint32_t marker_count;
    std::uint64_t device_timestamp_us;
    std::uint64_t computer_monotonic_ns;
    std::uint8_t strobe_left;
    std::uint8_t strobe_right;
    std::uint8_t reserved1[6];
    PointRecord left_points[kMaxRawPoints];
    PointRecord right_points[kMaxRawPoints];
    FiducialRecord fiducials[kMaxFiducials];
    MarkerRecord markers[kMaxMarkers];
};

struct PoseSlot {
    std::int64_t sequence;
    std::uint32_t frame_counter;
    std::uint32_t marker_count;
    std::uint64_t device_timestamp_us;
    std::uint64_t computer_monotonic_ns;
    MarkerRecord markers[kMaxMarkers];
};
#pragma pack(pop)

constexpr std::size_t kImageMetaOffset = kHeaderRegionBytes;
constexpr std::size_t kImageDataOffset = kImageMetaOffset + kImageSlotCount * kImageSlotMetaBytes;
constexpr std::size_t kImageDataPerSlot = 2 * kMaxImageBytes;
constexpr std::size_t kPoseRingOffset = kImageDataOffset + kImageSlotCount * kImageDataPerSlot;
constexpr std::size_t kTotalBytes = kPoseRingOffset + kPoseRingCapacity * sizeof(PoseSlot);

static_assert(sizeof(SharedHeader) <= kHeaderRegionBytes);
static_assert(sizeof(ImageSlotMeta) <= kImageSlotMetaBytes);

inline std::size_t image_meta_offset(std::size_t slot) {
    return kImageMetaOffset + slot * kImageSlotMetaBytes;
}

inline std::size_t left_image_offset(std::size_t slot) {
    return kImageDataOffset + slot * kImageDataPerSlot;
}

inline std::size_t right_image_offset(std::size_t slot) {
    return left_image_offset(slot) + kMaxImageBytes;
}

}  // namespace atrbridge
