#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "AudioGeneratorMP3.h"
#include "AudioFileSourceBuffer.h"

static uint32_t now = 100;
uint32_t millis() { return now; }
void delay(uint32_t milliseconds) { now += milliseconds; }

namespace m5 {
// Hardware boundary only: the real production adapter still owns PCM buffering,
// acceptance tracking, decoded-audio tracking and the queue-drain deadline.
class Speaker_Class {
 public:
  bool accept = true;
  bool stuck = false;
  size_t blocks = 0;
  size_t samples = 0;
  unsigned queuedTicks = 0;

  bool playRaw(const int16_t*, size_t count, uint32_t, bool, int, uint8_t) {
    if (accept) {
      ++blocks;
      samples += count / 2;
      queuedTicks = 3;
    }
    return accept;
  }
  bool isPlaying(uint8_t) {
    if (stuck) return true;
    if (!queuedTicks) return false;
    --queuedTicks;
    return true;
  }
  void stop(uint8_t) { queuedTicks = 0; }
};
}  // namespace m5

#include "driver/AudioOutputM5Speaker.h"
#include "driver/Mp3Playback.h"

// libmad/config.h defines NDEBUG: do not use assert(), which would silently
// remove these regressions from the build.
static void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

class Source final : public AudioFileSource {
 public:
  explicit Source(std::vector<uint8_t> content)
      : bytes(std::move(content)), advertised(bytes.size()) {}
  std::vector<uint8_t> bytes;
  size_t advertised;
  size_t position = 0;
  bool opened = true;

  uint32_t read(void* target, uint32_t count) override {
    count = std::min<size_t>(count, bytes.size() - position);
    if (count) std::memcpy(target, bytes.data() + position, count);
    position += count;
    return count;
  }
  bool close() override { opened = false; return true; }
  bool isOpen() override { return opened; }
  uint32_t getPos() override { return position; }
  uint32_t getSize() override { return advertised; }
};

class ObservedDecoder final : public AudioGeneratorMP3 {
 public:
  bool hasStatusCallback = false;
  bool RegisterStatusCB(AudioStatus::statusCBFn callback, void* context) override {
    hasStatusCallback = callback != nullptr;
    return AudioGeneratorMP3::RegisterStatusCB(callback, context);
  }
};

static std::vector<uint8_t> load(const char* path) {
  std::ifstream input(path, std::ios::binary);
  require(input.good(), std::string("fixture unavailable: ") + path);
  return {std::istreambuf_iterator<char>(input), {}};
}

static bool decode(Source& source, ObservedDecoder& decoder,
                   AudioOutputM5Speaker& output) {
  AudioFileSourceBuffer buffer(&source, 30 * 1024);
  // The function under test is the real production helper, not a copy of its
  // loop, callback policy, EOF inference or completion expression.
  const bool result = stackchan::decodeMp3(decoder, buffer, output, true);
  require(!decoder.hasStatusCallback, "decoder retains stack-local callback");
  return result;
}

static void check(const char* name, const std::vector<uint8_t>& data,
                  bool expected, bool rejectOutput = false, bool stall = false) {
  Source source(data);
  m5::Speaker_Class speaker;
  speaker.accept = !rejectOutput;
  speaker.stuck = stall;
  AudioOutputM5Speaker output(&speaker);
  ObservedDecoder decoder;
  const uint32_t started = now;
  const bool result = decode(source, decoder, output);
  require(result == expected, std::string(name) + ": wrong playback result");
  if (expected) {
    require(output.hasDecodedAudio(), std::string(name) + ": no decoded frame");
    require(output.playbackDelivered(), std::string(name) + ": PCM was not drained");
    require(speaker.samples > 1000, std::string(name) + ": only initial zero sample");
  }
  if (stall) require(now - started >= 2000, "queue stall did not reach deadline");
  std::printf("PASS %s decoded=%d samples=%zu\n", name, output.hasDecodedAudio(), speaker.samples);
}

int main(int argc, char** argv) {
  try {
    require(argc == 3, "expected standard-ID3 MP3 and raw MP3 paths");
    const auto normal = load(argv[1]);
    const auto raw = load(argv[2]);
    require(normal.size() > 2048 && normal[0] == 'I' && normal[1] == 'D'
                && normal[2] == '3', "standard fixture must contain ID3v2");
    require(raw.size() > 2048 && !(raw[0] == 'I' && raw[1] == 'D' && raw[2] == '3'),
            "raw fixture unexpectedly contains ID3v2");
    check("normal-id3", normal, true);
    check("raw", raw, true);
    check("empty", {}, false);
    check("one-zero-byte", {0}, false);
    check("zeros-1k", std::vector<uint8_t>(1024, 0), false);
    check("ascii-1k", std::vector<uint8_t>(1024, 'X'), false);
    check("empty-id3-header", {'I', 'D', '3', 4, 0, 0, 0, 0, 0, 0}, false);
    size_t tagLength = 10;
    size_t payloadLength = 0;
    for (size_t i = 6; i < 10; ++i) {
      require(normal[i] < 128, "invalid synchsafe ID3 size in fixture");
      payloadLength = (payloadLength << 7) | normal[i];
    }
    tagLength += payloadLength;
    require(tagLength < normal.size(), "fixture contains no MP3 after ID3");
    check("actual-tag-only", {normal.begin(), normal.begin() + tagLength}, false);
    auto corrupted = normal;
    std::fill_n(corrupted.begin() + corrupted.size() / 2, 512, 0);
    check("midstream-corruption", corrupted, false);
    check("output-rejected", normal, false, true);
    check("queue-stalled", normal, false, false, true);

    // Same decoder/output after a successful turn: an empty source must not
    // inherit the previous turn's decoded-audio marker or accepted PCM state.
    m5::Speaker_Class speaker;
    AudioOutputM5Speaker output(&speaker);
    ObservedDecoder decoder;
    Source valid(normal);
    require(decode(valid, decoder, output), "reuse setup failed");
    require(output.hasDecodedAudio(), "reuse setup did not decode audio");
    Source empty({});
    require(!decode(empty, decoder, output), "empty turn inherited prior completion");
    require(!output.hasDecodedAudio(), "decoded marker survived begin()");
    Source validAgain(normal);
    require(decode(validAgain, decoder, output), "valid turn after empty did not recover");
    std::puts("PASS valid-empty-valid-reuse");

    Source closed(normal);
    closed.opened = false;
    require(!decode(closed, decoder, output), "closed source was accepted");
    std::puts("PASS begin-failure-callback-cleanup");

    // Preserve the real OpenAITTS transport boundary alongside the decoder:
    // source EOF alone cannot distinguish a complete response from disconnect.
    // This calls its production predicate, never a copied length expression.
    Source shortened(normal);
    shortened.bytes.resize(shortened.bytes.size() / 2);
    const bool shortenedDecoded = decode(shortened, decoder, output);
    const bool shortenedComplete = stackchan::mp3BodyComplete(
        shortened.getSize(), shortened.getPos());
    require(!shortenedComplete, "truncated HTTP response marked complete");
    require(!(shortenedDecoded && shortenedComplete), "truncated HTTP acknowledged");
    require(!stackchan::mp3BodyComplete(0, 0), "empty body acknowledged");
    require(!stackchan::mp3BodyComplete(UINT32_MAX, normal.size()),
            "unknown content length acknowledged");
    require(stackchan::mp3BodyComplete(normal.size(), normal.size()),
            "complete finite body was rejected");
    std::puts("PASS http-completion-boundary");
    return 0;
  } catch (const std::exception& error) {
    std::fprintf(stderr, "FAIL %s\n", error.what());
    return 1;
  }
}
