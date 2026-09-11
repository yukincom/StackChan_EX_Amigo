#include <atomic>
#include <cassert>
#include <string>
#include <thread>
#include <vector>
#include "SpeechText.h"

using m5avatar::SpeechText;

int main() {
  SpeechText text;
  assert(text.snapshot().empty());

  // The HTTP error path passes a temporary String.c_str(). Retain both short
  // stack-backed and long heap-backed strings after their owner is destroyed.
  for (unsigned length : {3, 2048}) {
    const std::string expected = "接続エラー: " + std::string(length, 'A');
    {
      std::string temporary = expected;
      text.set(temporary.c_str());
      temporary.assign(length * 2, 'X');
    }
    std::vector<std::string> reused(64, std::string(length, 'Z'));
    assert(reused.front().size() == length);
    assert(text.snapshot() == expected);
  }
  {
    char temporary[] = "error";
    text.set(temporary);
    temporary[0] = 'X';
  }
  assert(text.snapshot() == "error");

  // A frame stays intact through long/short updates and clears. The borrowed
  // pointer used by DrawContext stays valid while its snapshot is in scope.
  const std::string frame = text.snapshot();
  const char* framePointer = frame.c_str();
  text.set(std::string(4096, 'B').c_str());
  assert(text.snapshot() == std::string(4096, 'B'));
  text.set("次の表示");
  assert(text.snapshot() == "次の表示");
  text.set("");
  assert(text.snapshot().empty());
  text.set(nullptr);
  assert(text.snapshot().empty());
  assert(std::string(framePointer) == "error");

  // Preserve Avatar's existing copy API without sharing storage or locks.
  text.set("コピー元");
  SpeechText copied(text), assigned;
  assigned = text;
  SpeechText& alias = text;
  text = alias;
  assert(text.snapshot() == "コピー元");
  text.set("変更後");
  assert(copied.snapshot() == "コピー元");
  assert(assigned.snapshot() == "コピー元");

  // Multiple setters may run alongside the drawing task. A frame must be a
  // complete published message, never a mixture or a stale borrowed pointer.
  const std::vector<std::string> messages = {
      "", "短い表示", std::string(4096, 'A'), std::string(8192, 'B')};
  text.set("");
  std::atomic<bool> start{false};
  std::vector<std::thread> workers;
  for (unsigned writer = 0; writer < 2; ++writer) {
    workers.emplace_back([&, writer] {
      while (!start.load()) std::this_thread::yield();
      for (unsigned i = 0; i < 10000; ++i) {
        text.set(messages[(i + writer) % messages.size()].c_str());
      }
    });
  }
  for (unsigned reader = 0; reader < 2; ++reader) {
    workers.emplace_back([&] {
      while (!start.load()) std::this_thread::yield();
      for (unsigned i = 0; i < 10000; ++i) {
        const std::string snapshot = text.snapshot();
        bool known = false;
        for (const auto& message : messages) known |= snapshot == message;
        assert(known);
        std::this_thread::yield();
        assert(std::string(snapshot.c_str()) == snapshot);
      }
    });
  }
  start.store(true);
  for (auto& worker : workers) worker.join();
}
