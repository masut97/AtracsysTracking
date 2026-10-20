#include "shared_protocol.hpp"
#include "frame_policy.hpp"

#include <cstring>
#include <iostream>

using namespace atrbridge;

// Independent wire-contract expectations, including timestamp field positions.
static_assert(sizeof(SharedHeader) == 668);
static_assert(sizeof(PointRecord) == 16);
static_assert(sizeof(FiducialRecord) == 56);
static_assert(sizeof(MarkerRecord) == 76);
static_assert(sizeof(ImageSlotMeta) == 32340);
static_assert(sizeof(PoseSlot) == 9760);
static_assert(offsetof(SharedHeader, heartbeat_ns) == 72);
static_assert(offsetof(SharedHeader, reserved0) == 36);
static_assert(recoverable_image_error(13));
static_assert(!recoverable_image_error(0));
static_assert(!recoverable_image_error(1));
static_assert(!recoverable_image_error(14));
static_assert(offsetof(ImageSlotMeta, device_timestamp_us) == 60);
static_assert(offsetof(ImageSlotMeta, computer_monotonic_ns) == 68);
static_assert(offsetof(PoseSlot, device_timestamp_us) == 16);
static_assert(offsetof(PoseSlot, computer_monotonic_ns) == 24);
static_assert(kPoseRingOffset == 50532352);
static_assert(kTotalBytes == 55529472);

int main() {
    PoseSlot source{};
    source.sequence = 7;
    source.frame_counter = 42;
    source.marker_count = 1;
    source.device_timestamp_us = 123456789;
    source.computer_monotonic_ns = 987654321;
    source.markers[0].geometry_id = 299;
    source.markers[0].translation_mm[2] = 12.5f;
    char wire[sizeof(PoseSlot)]{};
    std::memcpy(wire, &source, sizeof(source));
    PoseSlot decoded{};
    std::memcpy(&decoded, wire, sizeof(decoded));
    if (decoded.sequence != 7 || decoded.frame_counter != 42 ||
        decoded.device_timestamp_us != 123456789 ||
        decoded.computer_monotonic_ns != 987654321 ||
        decoded.markers[0].geometry_id != 299 ||
        decoded.markers[0].translation_mm[2] != 12.5f) return 1;
    for (std::size_t i = 0; i < kImageSlotCount; ++i) {
        if (image_meta_offset(i) + sizeof(ImageSlotMeta) > kImageDataOffset ||
            left_image_offset(i) + kMaxImageBytes != right_image_offset(i) ||
            right_image_offset(i) + kMaxImageBytes > kPoseRingOffset) return 2;
    }
    std::cout << "Synthetic bridge protocol checks passed\n";
    return 0;
}
