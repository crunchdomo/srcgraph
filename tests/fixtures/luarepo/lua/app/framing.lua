local M = {}
local Codec = {}

function Codec.encode(x)
  return tostring(x)
end

function M.pack(frame)
  local helper = function(v) return Codec.encode(v) end
  return helper(frame)
end

function standalone()
  return M.pack(1)
end

return M
