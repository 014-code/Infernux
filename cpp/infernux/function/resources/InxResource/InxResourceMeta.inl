#pragma once

#include <core/log/InxLog.h>

namespace infernux
{

// ----------------------------------
// Template Function Implementations
// ----------------------------------

template <typename T>
T InxResourceMeta::GetDataAs(const std::string &key) const
{
    if constexpr (std::is_same_v<T, std::string>)
        return GetStringData(key);
    else if constexpr (std::is_same_v<T, int>)
        return GetIntData(key);
    else if constexpr (std::is_same_v<T, bool>)
        return GetBoolData(key);
    else if constexpr (std::is_same_v<T, size_t>)
        return GetSizeData(key);
    else if constexpr (std::is_same_v<T, float>)
        return GetFloatData(key);
    else {
        static_assert(!sizeof(T), "InxResourceMeta::GetDataAs does not support this metadata type");
    }
}

} // namespace infernux
