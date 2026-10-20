#pragma once
#include <cstdint>
namespace atrbridge {
// Opt-in diagnostic carried in the otherwise unused v2 header word.
constexpr std::uint32_t kSegmentationOverflow = 0x5347000d;
constexpr bool recoverable_image_error(int sdk_status) { return sdk_status == 13; }
}
