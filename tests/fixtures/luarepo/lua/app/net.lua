-- Sends frames to the server.
--
-- Second paragraph, not part of the doc line.
local framing = require('app/framing')
local M = {}
local sent = 0

-- Counts one frame.
local function bump(n)
  sent = sent + n
  return sent
end

-- Sends one frame; the count goes up.
function M.send(frame)
  if frame then
    bump(1)
  end
  return framing.pack(frame)
end

function M:flush(queue)
  for _, f in ipairs(queue) do
    self.send(f)
    queue.clear()
  end
  log("flushed")
  return string.format("%d", sent)
end

M.reset = function()
  sent = 0
end

local function unused()
  return nil
end

M.count = bump
M.bump = bump

return M
